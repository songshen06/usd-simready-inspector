# NVIDIA Light External template (not committed)

`build_nvidia_deck.py` needs `NVIDIA_PPT_Light_External_v1.potx` from the **`create-nvidia-presentation`** skill (or any licensed copy of the same file).

On your machine, create a **symlink** (or copy) here:

```bash
cd presentation
mkdir -p vendor
ln -sf "$HOME/src/create-nvidia-presentation/skills/create-nvidia-presentation/assets/NVIDIA_PPT_Light_External_v1.potx" \
  vendor/NVIDIA_PPT_Light_External_v1.potx
```

Adjust the source path if your skill lives elsewhere. After the file is present, regenerate the NVIDIA deck from the repo root:

```bash
cd presentation
python3 -m venv .venv
.venv/bin/pip install -r requirements-deck.txt
.venv/bin/python build_flow_pptx.py
# From your create-nvidia-presentation skill scripts directory:
# .venv/bin/python build_nvidia_deck.py --manifest ../manifest-nvidia.json --output ../usd-simready-inspector-nvidia-template.pptx
```

Or use one command with `PYTHONPATH` if you call the script from the skill (see `presentation/README.md`).
