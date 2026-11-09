#!/bin/zsh
# Installation d'Écoute intelligente sur un Mac Apple Silicon (MacBook Air, Mac mini…)
set -e
cd "$(dirname "$0")"
[[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

[[ "$(uname -m)" == "arm64" ]] || { echo "Mac Apple Silicon (M1 ou plus récent) requis."; exit 1; }
command -v brew >/dev/null || { echo "Installez Homebrew : https://brew.sh"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v uv >/dev/null || brew install uv
[[ -d .venv ]] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python mlx-whisper sounddevice numpy

chmod +x bin/ecoute
mkdir -p "$HOME/.local/bin" && ln -sf "$PWD/bin/ecoute" "$HOME/.local/bin/ecoute"
for rc in "$HOME/.zshrc" "$HOME/.zprofile"; do
  grep -qs '\.local/bin' "$rc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
done

if [[ -n "$ECOUTE_SANS_FINDER" ]]; then
  .venv/bin/python -c "from ecoute.cli import initialiser; initialiser()"
else
  bin/ecoute dossiers >/dev/null
fi

echo "Préchargement du modèle Whisper (≈1,6 Go, une seule fois)…"
.venv/bin/python -c "from huggingface_hub import snapshot_download as d; d('mlx-community/whisper-large-v3-turbo')"

if ! command -v claude >/dev/null; then
  echo "Installation de Claude Code…"
  curl -fsSL https://claude.ai/install.sh | bash || echo "⚠️  Claude Code absent : curl -fsSL https://claude.ai/install.sh | bash"
fi

# Surveillance automatique en arrière-plan (dossiers 1_Audio et 3_Valide)
PLIST="$HOME/Library/LaunchAgents/com.thomaslefevre.ecoute.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.thomaslefevre.ecoute</string>
    <key>ProgramArguments</key>
    <array>
        <string>$HOME/.local/bin/ecoute</string>
        <string>surveiller</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/EcouteIntelligente/.surveillance.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/EcouteIntelligente/.surveillance.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/com.thomaslefevre.ecoute" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "✅ Installé et surveillance active en arrière-plan. En consultation, lancez : ecoute consultation"
