#!/usr/bin/env bash
# Regenerate usd-simready-inspector-nvidia-template.pptx using create-nvidia-presentation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SKILL_SCRIPTS="${SKILL_SCRIPTS:-${HOME}/src/create-nvidia-presentation/skills/create-nvidia-presentation/scripts}"
VENV="${ROOT}/.venv"
if [ ! -f "${ROOT}/vendor/NVIDIA_PPT_Light_External_v1.potx" ]; then
  echo "Missing template. See vendor/README.md (symlink or copy NVIDIA_PPT_Light_External_v1.potx)." >&2
  exit 1
fi
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q -r "${ROOT}/requirements-deck.txt"
(
  cd "$SKILL_SCRIPTS"
  "$VENV/bin/python" build_nvidia_deck.py \
    --manifest "${ROOT}/manifest-nvidia.json" \
    --output "${ROOT}/usd-simready-inspector-nvidia-template.pptx"
  "$VENV/bin/python" qa_nvidia_deck.py "${ROOT}/usd-simready-inspector-nvidia-template.pptx"
)
echo "OK: ${ROOT}/usd-simready-inspector-nvidia-template.pptx"
