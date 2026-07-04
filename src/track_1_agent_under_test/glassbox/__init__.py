"""
Glassbox — deterministic shell for the CAR-bench Track-1 agent.

Build order (see Bauplan Stufen 1–7):
  1. Ledger       — provenance tracking
  2. StateMachine — fixed state flow, zero free-running LLM
  3. Capability   — hallucination prevention
  4. Policies     — 19 rules as deterministic predicates
  5. Guard        — fabrication blocking
  6. Disambiguation — ambiguity resolution
  7. Auditor      — targeted self-check at two points
"""
from .ledger import Ledger, LedgerEntry
from .state_machine import (
    Action, EmitText, EmitToolCalls, PlannedCall, State, StateMachine, TurnContext,
)
from .capability import CapabilityMatcher, CapabilityIndex
from .policies import PolicyChecker, PolicyViolation, ALL_POLICIES
from .guard import FabricationGuard, GuardResult
from .disambiguation import DisambiguationEngine, DisambiguationResult
from .auditor import Auditor, AuditResult

__all__ = [
    "Ledger", "LedgerEntry",
    "StateMachine", "State", "TurnContext",
    "Action", "EmitText", "EmitToolCalls", "PlannedCall",
    "CapabilityMatcher", "CapabilityIndex",
    "PolicyChecker", "PolicyViolation", "ALL_POLICIES",
    "FabricationGuard", "GuardResult",
    "DisambiguationEngine", "DisambiguationResult",
    "Auditor", "AuditResult",
]
