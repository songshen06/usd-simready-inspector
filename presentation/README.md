# USD SimReady Inspector — Intro Decks

Intro slides for [github.com/songshen06/usd-simready-inspector](https://github.com/songshen06/usd-simready-inspector): **flow** (two pipeline diagrams) and **NVIDIA Light External** (cover, content, cumulative-commit line chart, closing).

## Files

| File | Role |
|------|------|
| `usd-simready-inspector-flow.pptx` | Two 16:9 slides: general inspection pipeline + static furniture pipeline (aligned with `../ARCHITECTURE_AND_FLOW.md`). |
| `manifest-nvidia.json` | Input for `build_nvidia_deck.py` from the **create-nvidia-presentation** skill. |
| `usd-simready-inspector-nvidia-template.pptx` | Built deck: cover, agenda, overview, two-column summary, section divider, **line chart (template slide 23)**, closing. |
| `build_flow_pptx.py` | Fallback: regenerates the flow deck with `python-pptx` only. |
| `html-flow/` | **Preferred:** HTML slides + `build-flow-html2pptx.js` (pptx skill **html2pptx**). |
| `rebuild_nvidia.sh` | Regenerates the NVIDIA-template deck (needs skill path + `vendor/` template). |
| `requirements-deck.txt` | Minimal Python deps for `build_flow_pptx.py` and running the skill scripts. |

## NVIDIA template (`vendor/`)

The manifest references `vendor/NVIDIA_PPT_Light_External_v1.potx`. That file is **not committed** (license). Add a symlink or copy from your installed **create-nvidia-presentation** skill—see `vendor/README.md`.

## Line chart data

The manifest uses **cumulative commit counts** by author date (snapshot from `git log`). Refresh before publishing:

```bash
cd ..
git log --reverse --format='%ad' --date=short | sort | uniq -c
```

Edit `manifest-nvidia.json` → slide with `"template_slide": 23` → `chart.categories` and `chart.series[0].values` (cumulative sums).

## Regenerate (from this directory)

```bash
cd presentation
# Flow deck (html2pptx — requires pptx skill scripts under ~/my-agent-skills/skills/pptx/scripts with npm deps)
node html-flow/build-flow-html2pptx.js

python3 -m venv .venv
.venv/bin/pip install -r requirements-deck.txt
./rebuild_nvidia.sh
```

Fallback without Node/html2pptx:

```bash
.venv/bin/python build_flow_pptx.py
```

Override the skill location if needed:

```bash
SKILL_SCRIPTS=/path/to/create-nvidia-presentation/skills/create-nvidia-presentation/scripts ./rebuild_nvidia.sh
```

## Combine into one deck (PowerPoint)

1. Open `usd-simready-inspector-nvidia-template.pptx`.
2. **Home → New Slide → Reuse Slides** → browse to `usd-simready-inspector-flow.pptx`.
3. Insert the two flow slides after the cover or agenda, reorder as needed.
4. Save as `usd-simready-inspector-intro-combined.pptx`.

## html2pptx note

Flow slides are generated with **`html-flow/build-flow-html2pptx.js`**, which loads **`html2pptx.js`** from your **`pptx`** skill (`~/my-agent-skills/skills/pptx/scripts`). Override with `PPTX_HTML2PPTX_SCRIPTS=/path/to/pptx/scripts` if the skill lives elsewhere.

Ensure that directory has `npm install` for `pptxgenjs`, `playwright`, `sharp`, etc., and run `npx playwright install chromium` once if needed.
