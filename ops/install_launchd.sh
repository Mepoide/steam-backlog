#!/usr/bin/env bash
# Instala (o reinstala) el job diario y el bot como servicios launchd del usuario (macOS).
set -euo pipefail
[ "$(uname)" = "Darwin" ] || { echo "Solo para macOS (launchd)."; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
mkdir -p "$DEST" "$REPO/data/logs"

for name in daily bot; do
  label="com.steambacklog.$name"
  out="$DEST/$label.plist"
  sed "s|__REPO__|$REPO|g" "$REPO/ops/launchd/$label.plist" > "$out"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$out"
  echo "✓ $label cargado ($out)"
done
echo "Logs en $REPO/data/logs/. Reinicia el bot con: launchctl kickstart -k $DOMAIN/com.steambacklog.bot"
