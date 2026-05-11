# NVIDIA Light External template (local only, not committed)

Place **`NVIDIA_PPT_Light_External_v1.potx`** here (symlink or copy from your **`create-nvidia-presentation`** skill).

Example:

```bash
ln -sf "$HOME/src/create-nvidia-presentation/skills/create-nvidia-presentation/assets/NVIDIA_PPT_Light_External_v1.potx" \
  "$(dirname "$0")/NVIDIA_PPT_Light_External_v1.potx"
```

Then from **`presentation/build/`** run **`./rebuild-all.sh`** (see `../build/README.md`).
