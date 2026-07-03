#!/usr/bin/env bash
# Compile main.tex → main.pdf and check page count.
# Install LaTeX first: sudo apt install texlive-full
set -euo pipefail

cd "$(dirname "$0")"

pdflatex -interaction=nonstopmode main.tex
bibtex main
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex

PAGES=$(pdfinfo main.pdf 2>/dev/null | awk '/^Pages:/{print $2}')
echo "--- PDF compiled: ${PAGES} pages ---"
if [ "${PAGES:-0}" -gt 6 ]; then
    echo "WARNING: ${PAGES} pages — competition limit is 4 content pages + refs"
    exit 1
fi
echo "OK: within page limit"
