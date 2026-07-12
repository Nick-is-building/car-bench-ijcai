"""
Zustandsmaschine — Stufe 2.

INTAKE → CAPABILITY_CHECK → (CLARIFY | PLAN) → POLICY_CHECK → EXECUTE → VERIFY → RESPOND

Das LLM laeuft nie frei: jeder Zustand hat ein eigenes enges Prompt-Modul mit
Temperatur 0 und JSON-Schema-Output. Die Maschine ist RESUMIERBAR, weil das
A2A-Protokoll multi-turn ist: Tool-Calls werden als Aktion an die A2A-Schicht
zurueckgegeben (EmitToolCalls), der Evaluator fuehrt sie aus und schickt die
Ergebnisse in einer neuen Nachricht — dann setzt `resume()` am EXECUTE-Punkt
fort, statt bei INTAKE neu zu starten.

PLAN→POLICY_CHECK→EXECUTE laeuft als begrenzte Schleife (siehe ADR-0002):
der Planner liefert pro Runde nur die sofort ausfuehrbaren Schritte; Schritte,
deren Argumente von Ergebnissen abhaengen, kommen in der naechsten Runde.
Leerer Plan = Turn-Toolarbeit abgeschlossen → VERIFY → RESPOND.

Idempotenz: deterministische call_ids (turn/runde/index), und ein identischer
(tool, argumente)-Call wird innerhalb eines Turns nie doppelt ausgefuehrt
(vgl. das "Sunroof zweimal geoeffnet"-Beispiel im Benchmark-Repo).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto

from loguru import logger as _log

from .ledger import Ledger


class State(Enum):
    INTAKE = auto()
    CAPABILITY_CHECK = auto()
    CLARIFY = auto()
    PLAN = auto()
    POLICY_CHECK = auto()
    EXECUTE = auto()
    VERIFY = auto()
    RESPOND = auto()
    DONE = auto()


# Upper bound for PLAN→EXECUTE rounds per user turn — last-resort loop stop,
# NOT a task-size budget. Published train tasks have up to 9 ground-truth
# actions (verified across all three splits, see ADR-0003), and read calls
# (get_weather, get_*_state) consume rounds on top of that, so the bound must
# sit well above 9+reads. Real planner loops are caught earlier by the
# duplicate-signature detection. TurnContext.plan_bound_hit flags if this
# bound ever binds so dev runs surface it.
MAX_PLAN_ROUNDS = 16

# Deterministic fallback texts (used only while later Stufen are stubs, or as
# last-resort safety). Honest, no fabricated alternatives.
FALLBACK_UNAVAILABLE = (
    "I'm sorry, I can't do that with the controls available in this car."
)
FALLBACK_POLICY_BLOCK = (
    "I'm sorry, I can't do that because it would conflict with a vehicle policy."
)


@dataclass
class PlannedCall:
    tool: str
    arguments: dict
    call_id: str
    rationale: str = ""

    @property
    def signature(self) -> str:
        return f"{self.tool}:{json.dumps(self.arguments, sort_keys=True)}"


class Action:
    """Base class for what the A2A layer must do next."""


@dataclass
class EmitToolCalls(Action):
    calls: list[PlannedCall]


@dataclass
class EmitText(Action):
    text: str


@dataclass
class TurnContext:
    """Everything the state machine needs for one user turn.

    Persists across A2A messages within the turn (tool-call round trips).
    """
    ledger: Ledger
    tools: list[dict]
    model: str
    current_state: State = State.INTAKE

    intent: dict = field(default_factory=dict)
    capability_result: str = ""          # "covered" | "uncovered" | "ambiguous"
    capability_missing: bool = False     # set by planner when required tool is absent
    # planner claimed a missing capability, but every named tool exists in the
    # schemas (or none was named) — claim rejected, re-plan (bounded)
    capability_claim_rebutted: bool = False
    capability_rebuttals: int = 0
    plan_round: int = 0
    pending_calls: list[PlannedCall] = field(default_factory=list)
    executed_signatures: set[str] = field(default_factory=set)
    policy_violations: list = field(default_factory=list)
    # markierte Pre-Flight-Notizen (Verschiebungen, Injektionen, Obligationen);
    # fliessen in die PLAN-Folgerunden und in VERIFY ein (ADR-0004)
    policy_notes: list[str] = field(default_factory=list)
    clarification_question: str = ""
    final_response: str = ""
    # true if MAX_PLAN_ROUNDS ended the turn before the planner confirmed
    # completion (empty plan) — a possible task cut-off, must be investigated
    plan_bound_hit: bool = False
    # state trace for tests/debugging — appended on every transition
    state_trace: list[str] = field(default_factory=list)
    # OI-011 diagnostics: tracks where a refusal originated
    capability_refusal_source: str = ""
    # OI-011 H-R2: intake re-extract already attempted this turn
    intake_rebuttal_done: bool = False
    # C1 guard-interface telemetry: every layer appends its GuardResult here
    layer_decisions: list = field(default_factory=list)
    # C2/C3/C4: provenance re-plan attempts before escalating to honesty sink
    provenance_rebuttals: int = 0
    # OI-017: enum-validation re-plan attempts before escalating to honesty sink
    enum_rebuttals: int = 0
    # Stufe 6: get_user_preferences already injected this turn (gather-once guard)
    preferences_gathered: bool = False
    # Stufe 6: silently resolved (tool, arg, value) tuples — telemetry
    disambiguation_resolved: list = field(default_factory=list)
    # F2: silent-refusal re-plan attempted (bounded to 1)
    silent_refusal_replan: bool = False
    # I1: value-flow re-plan attempts after disambiguation resolved a value
    # but the planner substituted a different one (bounded to 2)
    value_flow_rebuttals: int = 0

    def transition(self, state: State) -> None:
        self.current_state = state
        self.state_trace.append(state.name)


class StateMachine:
    """Orchestrates one agent turn through the fixed state sequence.

    `run_turn(ctx)` starts a turn after a user message.
    `resume(ctx)` continues after tool results were recorded in the ledger.
    Both return an Action for the A2A layer.
    """

    def run_turn(self, ctx: TurnContext) -> Action:
        from .capability import CapabilityIndex, CapabilityMatcher, fuzzy_catalog_hint
        from . import prompts

        ctx.transition(State.INTAKE)
        ctx.intent = prompts.intake.extract_intent(ctx)

        # INTAKE-REBUTTAL (OI-011 H-R2): one-time re-extract if required_tools or
        # required_but_missing_tools contain names not in the catalog but close to a
        # catalog tool (fuzzy or substring match).
        if not ctx.intake_rebuttal_done:
            idx = CapabilityIndex(ctx.tools)
            unknown_required = [
                t for t in ctx.intent.get("required_tools", [])
                if not idx.has_tool(t)
            ]
            # Also rebuttal when the LLM wrongly claims a capability is missing
            # (e.g. "navigation_delete_waypoint" instead of "delete_waypoint")
            unknown_missing = [
                t for t in ctx.intent.get("required_but_missing_tools", [])
                if not idx.has_tool(t)
            ]
            all_unknown = list(dict.fromkeys(unknown_required + unknown_missing))
            if all_unknown:
                hints: dict[str, list[str]] = {}
                for t in all_unknown:
                    candidates = fuzzy_catalog_hint(t, idx.tool_names)
                    if candidates:
                        hints[t] = candidates
                if hints:
                    ctx.intake_rebuttal_done = True
                    note = (
                        "INTAKE-REBUTTAL: some tool names are not in the catalog. "
                        + " ".join(
                            f"'{t}' → closest catalog tool: {' / '.join(c)}."
                            for t, c in hints.items()
                        )
                        + " Re-extract using exact catalog tool names. "
                        + "If a catalog tool covers the needed step, do NOT list it in "
                        + "required_but_missing_tools."
                    )
                    _log.info(
                        "INTAKE-REBUTTAL: re-extracting intent",
                        source="intake_required_tools+missing",
                        fuzzy_hints={t: c for t, c in hints.items()},
                    )
                    ctx.intent = prompts.intake.extract_intent(ctx, rebuttal_note=note)
                else:
                    _log.warning(
                        "INTAKE: unknown tool names with no fuzzy/substring match — will refuse",
                        source="intake_required_tools+missing",
                        unknown=all_unknown,
                    )

        ctx.transition(State.CAPABILITY_CHECK)
        matcher = CapabilityMatcher(ctx.tools)
        ctx.capability_result = self._capability_check(matcher, ctx)
        # C1 telemetry: capability layer verdict
        from .guard import GuardResult
        ctx.layer_decisions.append(GuardResult(
            verdict=(
                "BLOCK" if ctx.capability_result == "uncovered" else
                "UNCERTAIN" if ctx.capability_result == "ambiguous" else "PASS"
            ),
            layer="CapabilityMatcher",
            reason=ctx.capability_result,
        ))
        if ctx.capability_result == "uncovered":
            # OI-022: a RELATIONAL compound request ("sync the windows") is a
            # disambiguation case, not a missing capability — ask ONE targeted
            # question instead of refusing (deterministic gate, Lesson 1a).
            question = self._relational_request_clarification(ctx)
            if question is not None:
                _log.info(
                    "CAPABILITY: relational compound request — clarifying instead of refusing",
                    source="intake",
                    intent_missing=ctx.intent.get("required_but_missing_tools", []),
                )
                ctx.transition(State.CLARIFY)
                ctx.clarification_question = question
                return self._finish(ctx, question)
            ctx.capability_refusal_source = "intake"
            _log.warning(
                "Refusal: intake capability check uncovered",
                source="intake",
                intent_required_tools=ctx.intent.get("required_tools", []),
                intent_missing=ctx.intent.get("required_but_missing_tools", []),
            )
            return self._respond_refusal(ctx)

        if ctx.capability_result == "ambiguous":
            ctx.transition(State.CLARIFY)
            action = self._clarify(ctx)
            if action is not None:
                return action
            # ambiguity was resolved internally — fall through to PLAN

        return self._plan_execute_loop(ctx, matcher)

    def resume(self, ctx: TurnContext) -> Action:
        """Continue after the A2A layer recorded tool results in the ledger."""
        from .capability import CapabilityMatcher

        ctx.pending_calls = []
        matcher = CapabilityMatcher(ctx.tools)
        return self._plan_execute_loop(ctx, matcher)

    # --- PLAN → POLICY_CHECK → EXECUTE (bounded loop, ADR-0002) ---

    def _inject_preference_gather(self, ctx: TurnContext, inject_args: dict):
        """Emit a single get_user_preferences call and defer the turn.

        Shared by the pre-flight gather (Stufe 6) and the pre-plan gather
        (OI-016). Returns an EmitToolCalls action, or None when the gather
        already ran this turn (caller should fall through / re-plan).
        """
        from .guard import GuardResult
        ctx.preferences_gathered = True
        gather = PlannedCall(
            tool="get_user_preferences",
            arguments=inject_args,
            call_id=f"call_t{ctx.ledger.current_turn}_r{ctx.plan_round}_pref",
            rationale="disambiguation: retrieve learned preferences (Stufe 6)",
        )
        ctx.layer_decisions.append(GuardResult(
            verdict="UNCERTAIN", layer="Disambiguation.gather",
            reason="preferences required before resolving under-specified argument",
        ))
        ctx.transition(State.EXECUTE)
        if gather.signature not in ctx.executed_signatures:
            ctx.ledger.add_tool_call(gather.tool, gather.arguments, gather.call_id)
            ctx.executed_signatures.add(gather.signature)
            ctx.pending_calls = [gather]
            return EmitToolCalls([gather])
        return None

    def _plan_execute_loop(self, ctx: TurnContext, matcher) -> Action:
        from . import prompts

        while True:
            if ctx.plan_round >= MAX_PLAN_ROUNDS:
                ctx.plan_bound_hit = True
                break
            ctx.plan_round += 1
            ctx.transition(State.PLAN)
            steps = prompts.plan.build_plan(ctx)
            if not steps:
                if ctx.capability_missing:
                    ctx.capability_refusal_source = ctx.capability_refusal_source or "planner"
                    _log.warning(
                        "Refusal: planner reported missing capability",
                        source=ctx.capability_refusal_source,
                        policy_notes_count=len(ctx.policy_notes),
                    )
                    return self._respond_refusal(ctx)
                if ctx.capability_claim_rebutted and ctx.capability_rebuttals < 2:
                    # false missing-capability claim — re-plan with the
                    # PLAN-GUARD note instead of refusing (B6 root cause)
                    ctx.capability_rebuttals += 1
                    continue
                # OI-016: the planner cannot draft a call because a required
                # state-changing tool needs a preference-driven value (e.g. the
                # ambient-light color). Gather the preference, then re-plan so
                # the cascade can supply the value — instead of giving up here.
                from .disambiguation import DisambiguationEngine
                gather_args = DisambiguationEngine().pre_plan_gather(ctx)
                if gather_args is not None:
                    action = self._inject_preference_gather(ctx, gather_args)
                    if action is not None:
                        return action
                # F2: silent-refusal guard. The planner returned no steps and
                # did not flag capability_missing, but INTAKE identified
                # required tools that ARE in the catalog. One bounded re-plan
                # with an explicit note — the planner may have overlooked an
                # available tool (e.g. open_close_trunk_door).
                if (not ctx.silent_refusal_replan
                        and not ctx.executed_signatures
                        and ctx.capability_rebuttals == 0):
                    from .capability import CapabilityIndex
                    idx = CapabilityIndex(ctx.tools)
                    covered_required = [
                        t for t in ctx.intent.get("required_tools", [])
                        if idx.has_tool(t)
                    ]
                    if covered_required:
                        ctx.silent_refusal_replan = True
                        ctx.policy_notes.append(
                            "PLAN-GUARD: you returned no tool calls, but the "
                            "following tools ARE available in the catalog and "
                            "were identified as required: "
                            + ", ".join(covered_required)
                            + ". If the user's request can be fulfilled with "
                            "these tools, plan the necessary steps."
                        )
                        _log.info(
                            "Silent-refusal guard: re-plan with available tools",
                            covered_required=covered_required,
                        )
                        continue
                break

            calls: list[PlannedCall] = []
            unknown_tool_names: list[str] = []
            for i, step in enumerate(steps):
                call = PlannedCall(
                    tool=step["tool"],
                    arguments=step.get("arguments", {}),
                    call_id=f"call_t{ctx.ledger.current_turn}_r{ctx.plan_round}_s{i}",
                    rationale=step.get("rationale", ""),
                )
                # OI-016 Fix A: unknown-argument guard (Lesson 1a). The planner
                # sometimes emits an argument absent from the tool schema (e.g.
                # a duplicate `color` next to the valid `lightcolor`); the
                # evaluator raises a TypeError and the identical failing call
                # would loop to MAX_PLAN_ROUNDS. Strip every non-schema argument
                # before the call is validated or emitted — never silently:
                # each strip is a policy note AND a layer decision so the trace
                # shows exactly what code removed and why.
                if matcher.index.has_tool(call.tool):
                    unknown = [a for a in call.arguments
                               if not matcher.index.has_parameter(call.tool, a)]
                    if unknown:
                        from .guard import GuardResult
                        kept = {a: v for a, v in call.arguments.items()
                                if a not in unknown}
                        for a in unknown:
                            ctx.policy_notes.append(
                                f"stripped unknown argument {a!r}, not in schema "
                                f"for tool {call.tool}"
                            )
                        ctx.layer_decisions.append(GuardResult(
                            verdict="UNCERTAIN", layer="ArgumentSchema.unknown",
                            reason=f"stripped non-schema argument(s) {unknown} "
                                   f"from {call.tool}",
                        ))
                        _log.info(
                            "Unknown-argument guard: stripped non-schema args",
                            tool=call.tool, stripped=unknown,
                        )
                        call = PlannedCall(
                            tool=call.tool, arguments=kept,
                            call_id=call.call_id, rationale=call.rationale,
                        )
                # per-step capability guard: never emit a call the evaluator
                # has no tool for (deterministic, no LLM)
                if matcher.check_step(call.tool, call.arguments) == "uncovered":
                    unknown_tool_names.append(call.tool)
                    continue
                # idempotency: identical (tool, args) never executes twice per turn
                if call.signature in ctx.executed_signatures:
                    continue
                calls.append(call)

            if not calls:
                if unknown_tool_names:
                    # planner tried to USE unknown tool names in steps — log source
                    ctx.capability_refusal_source = "execute_guard"
                    _log.warning(
                        "Refusal: planner used unknown tool names in steps",
                        source="execute_guard",
                        unknown_tools=unknown_tool_names,
                    )
                    return self._respond_refusal(ctx)
                # everything was a duplicate → planner is looping; stop
                break

            # Stufe 4 (ADR-0004): declarative policy pre-flight. May refuse,
            # block, inject corrective calls, or defer calls to a later round.
            ctx.transition(State.POLICY_CHECK)
            pf = self._policy_pre_flight(ctx, calls, matcher)
            # C1 telemetry: policy layer verdict
            from .guard import FabricationGuard, GuardResult
            if pf.missing_capability:
                ctx.layer_decisions.append(GuardResult(
                    verdict="BLOCK", layer="PolicyChecker.preflight",
                    reason=f"missing capability: {pf.missing_capability}",
                ))
                ctx.policy_violations = pf.missing_capability
                ctx.capability_missing = True
                ctx.capability_refusal_source = "policy_pre_flight"
                _log.warning(
                    "Refusal: policy pre-flight missing capability",
                    source="policy_pre_flight",
                    missing=pf.missing_capability,
                )
                return self._respond_refusal(ctx)
            if pf.blocked:
                ctx.layer_decisions.append(GuardResult(
                    verdict="BLOCK", layer="PolicyChecker.preflight",
                    reason=f"blocked: {pf.blocked}",
                ))
                ctx.policy_violations = pf.blocked
                return self._respond_policy_block(ctx)
            if pf.confirmations:
                # OI-007: adverse-weather (or other) confirmation gate — deterministic
                # BLOCK whose correction is a targeted Rückfrage, not a refusal.
                ctx.layer_decisions.append(GuardResult(
                    verdict="BLOCK", layer="PolicyChecker.confirmation",
                    reason=f"confirmation required: "
                           f"{[c.policy_id for c in pf.confirmations]}",
                ))
                ctx.policy_violations = pf.confirmations
                return self._respond_confirmation(ctx, pf.confirmations)
            ctx.layer_decisions.append(GuardResult(
                verdict="PASS", layer="PolicyChecker.preflight",
                reason=f"notes={len(pf.notes)} injected={len(pf.injected)}",
            ))
            ctx.policy_notes.extend(pf.notes)
            calls = [
                PlannedCall(
                    tool=inj.tool,
                    arguments=inj.arguments,
                    call_id=f"call_t{ctx.ledger.current_turn}_r{ctx.plan_round}_p{i}",
                    rationale=f"policy pre-flight injection ({inj.policy_id})",
                )
                for i, inj in enumerate(pf.injected)
            ] + list(pf.kept)
            if not calls:
                # whole batch deferred — re-plan with the pre-flight notes
                continue

            # Stufe 6 (ADR-0005): disambiguation guard. May gather preferences,
            # silently override an under-specified argument (value-flow guarantee),
            # or ask ONE targeted clarification question.
            from .disambiguation import DisambiguationEngine
            dis = DisambiguationEngine().pre_flight(ctx, calls)
            if dis.inject_preferences is not None:
                action = self._inject_preference_gather(ctx, dis.inject_preferences)
                if action is not None:
                    return action
                # already fetched (defensive) — fall through to re-plan
                continue
            if dis.question:
                ctx.layer_decisions.append(GuardResult(
                    verdict="BLOCK", layer="Disambiguation.clarify",
                    reason="≥2 valid candidates remain — one clarification question",
                ))
                return self._respond_disambiguation(ctx, dis.question)
            if dis.resolved:
                ctx.disambiguation_resolved.extend(dis.resolved)
                ctx.layer_decisions.append(GuardResult(
                    verdict="PASS", layer="Disambiguation.resolve",
                    reason=f"silently resolved {len(dis.resolved)} arg(s)",
                ))
            calls = dis.calls

            # OI-017 (b): cross-turn tool-execution-error bound. A (tool, args)
            # call that already returned a FAILURE earlier in this conversation
            # is never re-emitted identically — each user turn starts a fresh
            # TurnContext, so executed_signatures alone cannot stop a repeat
            # across turns; the ledger persists and is authoritative.
            failed_sigs = ctx.ledger.failed_call_signatures()
            if failed_sigs:
                repeats = [c for c in calls if c.signature in failed_sigs]
                if repeats:
                    ctx.layer_decisions.append(GuardResult(
                        verdict="BLOCK", layer="ToolExecution.retry_bound",
                        reason=f"{len(repeats)} call(s) already failed — not retrying "
                               f"identically: {[c.tool for c in repeats]}",
                    ))
                    _log.warning(
                        "Tool-execution retry bound: identical failed call → honest sink",
                        tools=[c.tool for c in repeats],
                    )
                    return self._respond_tool_error(ctx)

            # OI-017 (a): deterministic enum validation against the tool schema.
            # The LLM proposes argument values; code checks each against the
            # schema's allowed `enum` (Lesson 1a). An invalid value (e.g.
            # "all windows" instead of "ALL") triggers a bounded corrective
            # re-plan, never an emitted call.
            enum_violations = [
                (call.tool, arg_name, arg_val, allowed)
                for call in calls
                for arg_name, arg_val in call.arguments.items()
                if (allowed := matcher.index.enum_values(call.tool, arg_name)) is not None
                and arg_val not in allowed
            ]
            if enum_violations:
                brief = [(t, a, v) for t, a, v, _ in enum_violations]
                if ctx.enum_rebuttals < 2:
                    ctx.enum_rebuttals += 1
                    for tool, arg_name, arg_val, allowed in enum_violations:
                        ctx.policy_notes.append(
                            f"INVALID-ARGUMENT: {tool}.{arg_name}={arg_val!r} is not "
                            f"an allowed value. Choose exactly one of: "
                            f"{', '.join(map(str, allowed))}. Use the exact schema "
                            f"token, not a natural-language phrase."
                        )
                    ctx.layer_decisions.append(GuardResult(
                        verdict="UNCERTAIN", layer="ArgumentSchema.enum",
                        reason=f"invalid enum value(s) → re-plan: {brief}",
                    ))
                    _log.info(
                        "Enum validation: invalid value → re-plan",
                        violations=brief, rebuttal=ctx.enum_rebuttals,
                    )
                    continue
                ctx.layer_decisions.append(GuardResult(
                    verdict="BLOCK", layer="ArgumentSchema.enum",
                    reason=f"invalid enum value(s) after 2 re-plans: {brief}",
                ))
                _log.warning(
                    "Enum validation: rebuttals exhausted → honest sink",
                    violations=brief,
                )
                return self._respond_invalid_argument(ctx, enum_violations)

            # Stufe 5 (C2/C3/C4): argument provenance check before emitting calls.
            # State-changing calls with numeric args must have ledger-verified bindings.
            fg = FabricationGuard()
            first_uncertain: tuple | None = None
            for call in calls:
                prov = fg.check_tool_arguments(call.tool, call.arguments, ctx.ledger, model=ctx.model)
                ctx.layer_decisions.append(prov)
                _log.debug(
                    "FabricationGuard provenance",
                    tool=call.tool, verdict=prov.verdict, layer=prov.layer,
                )
                if prov.verdict == "BLOCK":
                    _log.warning(
                        "FabricationGuard.C2: BLOCK — aborting call",
                        tool=call.tool, reason=prov.reason,
                    )
                    return self._respond_fabrication_block(ctx, prov, call)
                if prov.verdict == "UNCERTAIN" and first_uncertain is None:
                    first_uncertain = (call, prov)

            if first_uncertain is not None:
                call_unc, prov_unc = first_uncertain
                if ctx.provenance_rebuttals < 2:
                    ctx.provenance_rebuttals += 1
                    ctx.policy_notes.append(
                        f"PROVENANCE-REPLAN: argument binding uncertain — {prov_unc.reason}. "
                        f"Re-plan for {call_unc.tool} using only values the user explicitly "
                        f"stated for that specific item."
                    )
                    _log.info(
                        "FabricationGuard: UNCERTAIN → re-plan",
                        tool=call_unc.tool, rebuttal=ctx.provenance_rebuttals,
                    )
                    continue  # back to while-loop top → re-plan
                _log.warning(
                    "FabricationGuard: UNCERTAIN rebuttals exhausted → honesty sink",
                    tool=call_unc.tool,
                )
                return self._respond_provenance_sink(ctx, prov_unc, call_unc)

            # I1: value-throughflow check — the disambiguation cascade resolved
            # deterministic values; verify the planner hasn't substituted them.
            if ctx.disambiguation_resolved:
                mismatches = []
                for tool, arg, resolved_val in ctx.disambiguation_resolved:
                    for call in calls:
                        if call.tool == tool and arg in call.arguments:
                            if call.arguments[arg] != resolved_val:
                                mismatches.append((call, arg, resolved_val))
                if mismatches:
                    if ctx.value_flow_rebuttals < 2:
                        ctx.value_flow_rebuttals += 1
                        for call, arg, correct in mismatches:
                            ctx.policy_notes.append(
                                f"VALUE-FLOW: {call.tool}.{arg} must be "
                                f"{correct!r} (disambiguation-resolved), not "
                                f"{call.arguments[arg]!r}. Use the correct value."
                            )
                        ctx.layer_decisions.append(GuardResult(
                            verdict="UNCERTAIN",
                            layer="ValueFlow.check",
                            reason=f"planner substituted {len(mismatches)} "
                                   f"disambiguation-resolved value(s) → re-plan",
                        ))
                        _log.warning(
                            "Value-flow violation: planner substituted resolved value",
                            mismatches=[
                                (c.tool, a, c.arguments[a], v)
                                for c, a, v in mismatches
                            ],
                            rebuttal=ctx.value_flow_rebuttals,
                        )
                        continue
                    for call, arg, correct in mismatches:
                        call.arguments[arg] = correct
                    ctx.layer_decisions.append(GuardResult(
                        verdict="PASS",
                        layer="ValueFlow.force",
                        reason=f"rebuttals exhausted — forced {len(mismatches)} "
                               f"value(s) to disambiguation-resolved",
                    ))
                    _log.warning(
                        "Value-flow: rebuttals exhausted, forcing correct values",
                        mismatches=[
                            (c.tool, a, v) for c, a, v in mismatches
                        ],
                    )

            ctx.transition(State.EXECUTE)
            for call in calls:
                ctx.ledger.add_tool_call(call.tool, call.arguments, call.call_id)
                ctx.executed_signatures.add(call.signature)
            ctx.pending_calls = calls
            return EmitToolCalls(calls)

        return self._verify_and_respond(ctx)

    # --- terminal paths ---

    def _verify_and_respond(self, ctx: TurnContext) -> Action:
        from .guard import FabricationGuard, GuardResult, inject_unknown_caveat
        from .auditor import Auditor
        from . import prompts

        ctx.transition(State.VERIFY)
        draft = prompts.verify.draft_response(ctx)

        # Stufe 7: deterministic self-check of the draft's declared claims
        # (no LLM call of its own — parses the forced self-check in `draft.claims`).
        audit = Auditor().pre_response_check(draft, ctx.ledger,
                                              policy_notes=ctx.policy_notes)
        ctx.layer_decisions.append(GuardResult(
            verdict="BLOCK" if not audit.passed else "PASS",
            layer="Auditor.pre_response",
            reason=("; ".join(audit.issues) if audit.issues else "all claims backed"),
        ))

        fg = FabricationGuard()
        safe = fg.sanitize(audit.safe_text, ctx.ledger, model=ctx.model,
                           policy_notes=ctx.policy_notes)
        ctx.layer_decisions.append(GuardResult(
            verdict="BLOCK" if safe != audit.safe_text else "PASS",
            layer="FabricationGuard.C5",
            reason="draft modified" if safe != audit.safe_text else "draft clean",
        ))

        safe = inject_unknown_caveat(safe, ctx.ledger, ctx.executed_signatures)

        ctx.transition(State.RESPOND)
        final = prompts.respond.finalize(safe, ctx)
        return self._finish(ctx, final)

    def _respond_fabrication_block(self, ctx: TurnContext, result: "GuardResult",
                                    call: PlannedCall | None = None) -> Action:
        ctx.transition(State.RESPOND)
        tool = call.tool if call else ""
        action = tool.replace("_", " ") if tool else "that"
        argument = result.reason.split("argument ")[-1].split(" ")[0] if result.reason else ""
        if tool:
            text = (
                f"I'm not certain which value you'd like me to use for "
                f"'{argument}' when I {action}. Could you state it explicitly?"
                if argument else
                f"I'm sorry, I can't proceed with {action} — I don't have a "
                f"confirmed value to use. Could you clarify the exact value "
                f"you'd like me to set?"
            )
        else:
            text = (
                "I'm sorry, I can't proceed — I don't have a confirmed value "
                "to use. Could you clarify the exact value you'd like me to set?"
            )
        return self._finish(ctx, text)

    def _respond_provenance_sink(self, ctx: TurnContext, result: "GuardResult",
                                  call: PlannedCall | None = None) -> Action:
        ctx.transition(State.RESPOND)
        tool = call.tool if call else ""
        action = tool.replace("_", " ") if tool else "that"
        argument = result.reason.split("argument ")[-1].split(" ")[0] if result.reason else ""
        if tool:
            text = (
                f"I'm not certain which value you'd like me to use for "
                f"'{argument}' when I {action}. Could you state it explicitly?"
                if argument else
                f"I'm not certain which value you'd like me to use for {action}. "
                f"Could you confirm the exact setting you have in mind?"
            )
        else:
            text = (
                "I'm not certain which value you'd like me to use for that. "
                "Could you confirm the exact setting you have in mind?"
            )
        return self._finish(ctx, text)

    def _respond_invalid_argument(self, ctx: TurnContext, violations: list) -> Action:
        """OI-017 (a): the planner could not map a value to a valid schema enum
        even after two corrective re-plans — stop, don't emit a call the tool
        would reject, and ask the user for the exact setting (honest sink)."""
        ctx.transition(State.RESPOND)
        args = ", ".join(dict.fromkeys(a for _, a, _, _ in violations))
        text = (
            "I'm sorry — I couldn't complete that because I wasn't able to map "
            f"your request to a valid option for: {args}. "
            "Could you tell me exactly which setting you'd like?"
        )
        return self._finish(ctx, text)

    def _respond_tool_error(self, ctx: TurnContext) -> Action:
        """OI-017 (b): an identical call already failed in this conversation —
        stop retrying and be honest instead of looping."""
        ctx.transition(State.RESPOND)
        text = (
            "I'm sorry — that action keeps failing when I try it, so I've "
            "stopped retrying. Could you clarify exactly what you'd like me to do?"
        )
        return self._finish(ctx, text)

    def _respond_confirmation(self, ctx: TurnContext, confirmations: list) -> Action:
        """OI-007: emit the targeted confirmation question and end the turn.
        The user's reply is recorded in the ledger; next turn the same rule sees
        the confirmation and lets the call through."""
        ctx.transition(State.RESPOND)
        return self._finish(ctx, confirmations[0].question)

    def _respond_disambiguation(self, ctx: TurnContext, question: str) -> Action:
        """Stufe 6: ≥2 valid candidates remain for a state-changing argument —
        ask ONE targeted question. The user's answer arrives next turn and the
        planner re-plans with the now-explicit value."""
        ctx.clarification_question = question
        ctx.transition(State.RESPOND)
        return self._finish(ctx, question)

    def _respond_refusal(self, ctx: TurnContext) -> Action:
        if ctx.executed_signatures:
            # Work was done before hitting the missing capability.  Route
            # through VERIFY → Auditor → sanitize/C6 so the response
            # reflects what succeeded and C6 catches false inability claims.
            return self._verify_and_respond(ctx)

        from . import prompts

        ctx.transition(State.RESPOND)
        try:
            text = prompts.respond.generate_honest_refusal(ctx)
        except NotImplementedError:  # Stufe 3 pass-through
            text = FALLBACK_UNAVAILABLE
        return self._finish(ctx, text)

    def _respond_policy_block(self, ctx: TurnContext) -> Action:
        from . import prompts

        ctx.transition(State.RESPOND)
        try:
            text = prompts.respond.generate_policy_block(ctx)
        except NotImplementedError:  # Stufe 4 pass-through
            text = FALLBACK_POLICY_BLOCK
        return self._finish(ctx, text)

    def _finish(self, ctx: TurnContext, text: str) -> EmitText:
        ctx.final_response = text
        ctx.ledger.add_agent_response(text)
        ctx.transition(State.DONE)
        return EmitText(text)

    # --- stub-safe component calls (pass-through until their Stufe lands) ---

    def _capability_check(self, matcher, ctx: TurnContext) -> str:
        try:
            return matcher.check(ctx.intent)
        except NotImplementedError:  # Stufe 3 pass-through
            return "ambiguous" if ctx.intent.get("is_ambiguous") else "covered"

    def _clarify(self, ctx: TurnContext) -> Action | None:
        """Genuine goal/tool ambiguity (is_ambiguous) that no cascade layer can
        resolve. Under-specified argument VALUES are handled deterministically in
        the plan-loop disambiguation guard (Stufe 6), not here. Conservative
        default: ask the single question INTAKE already formulated."""
        question = ctx.intent.get("clarification_question", "").strip()
        if not question:
            reason = ctx.intent.get("ambiguity_reason", "your request")
            question = f"Just to make sure I get this right: could you clarify {reason}?"
        ctx.clarification_question = question
        return self._finish(ctx, question)

    _RELATIONAL_VERBS = frozenset({
        "sync", "synchronize", "synchronise", "match", "align", "mirror",
        "copy", "equalize", "equalise", "harmonize", "harmonise",
    })

    def _relational_request_clarification(self, ctx: TurnContext) -> str | None:
        """OI-022: a compound request named with a RELATIONAL verb ("sync the
        window positions") that no single tool covers is under-specified, not
        impossible — the state can be reached with individual setter calls.

        Deterministic gate, hallucination-safe by construction: fires ONLY when
        (a) EVERY unknown tool name starts with a relational verb — hallucination
        tasks remove REAL tools whose names keep standard verbs (set_/open_/
        get_…) and therefore still take the honest-refusal path — and (b) at
        least one non-getter catalog tool shares an object token with the
        unknown name (the request is actually expressible). Returns the one
        targeted question, or None → normal refusal.
        """
        from .capability import CapabilityIndex

        idx = CapabilityIndex(ctx.tools)
        unknown = [
            t for t in dict.fromkeys(
                list(ctx.intent.get("required_but_missing_tools") or [])
                + list(ctx.intent.get("required_tools") or [])
            )
            if t and not idx.has_tool(t)
        ]
        if not unknown:
            return None
        for name in unknown:
            tokens = [tok for tok in name.lower().split("_") if tok]
            if not tokens or tokens[0] not in self._RELATIONAL_VERBS:
                return None
            objects = set(tokens[1:])
            expressible = any(
                not c.startswith("get_") and (set(c.lower().split("_")) & objects)
                for c in idx.tool_names
            )
            if not expressible:
                return None
        subject = " ".join(tok for tok in unknown[0].lower().split("_") if tok)
        return (
            f"I don't have a single function to {subject}, but I can adjust "
            f"each setting individually. Could you tell me exactly which ones "
            f"you'd like me to change, and to what position or value?"
        )

    def _policy_pre_flight(self, ctx: TurnContext, calls: list[PlannedCall], matcher):
        from .policies import PolicyChecker

        pending = {
            t for t in (ctx.intent or {}).get("required_tools", [])
            if matcher.index.has_tool(t)
        }
        return PolicyChecker().pre_flight(calls, ctx.ledger, matcher.index,
                                          pending_tools=pending)
