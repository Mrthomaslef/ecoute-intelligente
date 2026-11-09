#!/bin/zsh
# Installation d'Écoute intelligente sur un Mac Apple Silicon (MacBook Air, Mac mini…)
set -e
cd "$(dirname "$0")"
[[ "$(uname -m)" == "arm64" ]] || { echo "Mac Apple Silicon (M1 ou plus récent) requis."; exit 1; }
command -v brew >/dev/null || { echo "Installez Homebrew : https://brew.sh"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v uv >/dev/null || brew install uv
[[ -d .venv ]] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python mlx-whisper sounddevice numpy
chmod +x bin/ecoute
mkdir -p "$HOME/.local/bin" && ln -sf "$PWD/bin/ecoute" "$HOME/.local/bin/ecoute"
bin/ecoute dossiers >/dev/null
echo "Préchargement du modèle Whisper (≈1,6 Go, une seule fois)…"
.venv/bin/python -c "from huggingface_hub import snapshot_download as d; d('mlx-community/whisper-large-v3-turbo')"
command -v claude >/dev/null || echo "⚠️  Claude Code absent : curl -fsSL https://claude.ai/install.sh | bash"
echo "✅ Installé. Lancez : ecoute surveiller"
