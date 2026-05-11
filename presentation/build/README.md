# Rebuilding the intro decks

All generator inputs are **only** in this directory:

| Path | Purpose |
|------|---------|
| `flow/*.html` | Flow slides (html2pptx). |
| `build-flow.js` | Writes `../usd-simready-inspector-flow.pptx`. |
| `manifest-nvidia.json` | NVIDIA Light External manifest → `../usd-simready-inspector-nvidia-template.pptx`. |
| `requirements-deck.txt` | Python deps for `build_nvidia_deck.py` / QA. |
| `vendor/` | Local symlink/copy of `NVIDIA_PPT_Light_External_v1.potx` (see `vendor/README.md`). |

**One command** (from this folder):

```bash
chmod +x rebuild-all.sh   # once
./rebuild-all.sh
```

Environment overrides:

- **`PPTX_HTML2PPTX_SCRIPTS`** — directory that contains `html2pptx.js` and its `node_modules` (default: `~/my-agent-skills/skills/pptx/scripts`).
- **`SKILL_SCRIPTS`** — directory that contains `build_nvidia_deck.py` (default: `~/src/create-nvidia-presentation/skills/create-nvidia-presentation/scripts`).

Refresh the commit chart: edit `manifest-nvidia.json` (slide `template_slide` **23**), using `git log --reverse --format='%ad' --date=short | sort | uniq -c` at the repo root.

Virtualenv is created as **`build/.venv`** (gitignored).
