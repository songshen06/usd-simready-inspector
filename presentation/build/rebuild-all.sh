#!/usr/bin/env bash
# Regenerate both .pptx files in the parent presentation/ folder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$ROOT/.." && pwd)"
SKILL_NV="${SKILL_SCRIPTS:-${HOME}/src/create-nvidia-presentation/skills/create-nvidia-presentation/scripts}"

if [[ ! -f "${ROOT}/vendor/NVIDIA_PPT_Light_External_v1.potx" ]]; then
  echo "Missing ${ROOT}/vendor/NVIDIA_PPT_Light_External_v1.potx — see vendor/README.md" >&2
  exit 1
fi

cd "$ROOT"
node build-flow.js

VENV="${ROOT}/.venv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -r "${ROOT}/requirements-deck.txt"

(
  cd "$SKILL_NV"
  "$VENV/bin/python" build_nvidia_deck.py \
    --manifest "${ROOT}/manifest-nvidia.json" \
    --output "${OUT}/usd-simready-inspector-nvidia-template.pptx"
  "$VENV/bin/python" qa_nvidia_deck.py "${OUT}/usd-simready-inspector-nvidia-template.pptx"
)

echo "OK: ${OUT}/usd-simready-inspector-flow.pptx"
echo "OK: ${OUT}/usd-simready-inspector-nvidia-template.pptx"
