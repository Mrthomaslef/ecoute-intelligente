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

# Boutons marche/arrêt (apps dans Applications + Dock) : 1 clic = écoute, 2e clic = fin
chmod +x bin/ecoute-bouton
APPS=/Applications; [[ -w $APPS ]] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; }
for v in "cr:Écoute Consultation" "rcp:Écoute RCP"; do
  t="${v%%:*}"; nom="${v#*:}"; app="$APPS/$nom.app"
  rm -rf "$app"
  osacompile -o "$app" -e "do shell script quoted form of \"$PWD/bin/ecoute-bouton\" & \" $t\""
  ic=$(mktemp -d)
  if qlmanage -t -s 512 -o "$ic" "assets/bouton-$t.svg" >/dev/null 2>&1; then
    mkdir "$ic/i.iconset"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$ic/bouton-$t.svg.png" --out "$ic/i.iconset/icon_${s}x${s}.png" >/dev/null
      sips -z $((s*2)) $((s*2)) "$ic/bouton-$t.svg.png" --out "$ic/i.iconset/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$ic/i.iconset" -o "$app/Contents/Resources/applet.icns"
    # macOS privilégie Assets.car (icône générique) : on le retire pour garder notre icône
    rm -f "$app/Contents/Resources/Assets.car"
    plutil -remove CFBundleIconName "$app/Contents/Info.plist" 2>/dev/null || true
    codesign --force --sign - "$app" 2>/dev/null || true
    touch "$app"
  fi
  rm -rf "$ic"
  if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "coute ${nom#* }\";"; then
    defaults write com.apple.dock persistent-apps -array-add \
      "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$app</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
  fi
done
LS=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
$LS -f "$APPS/Écoute Consultation.app" "$APPS/Écoute RCP.app" 2>/dev/null || true
rm -f "$(getconf DARWIN_USER_CACHE_DIR)com.apple.dock.iconcache"
killall Dock 2>/dev/null || true

echo "✅ Installé et surveillance active en arrière-plan."
echo "   En consultation : cliquez sur « Écoute Consultation » dans le Dock (recliquez pour terminer)."
