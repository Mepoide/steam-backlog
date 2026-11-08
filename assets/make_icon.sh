#!/bin/sh
# Regenera bot-icon.jpg (foto de perfil del bot) a partir de bot-icon.svg. Requiere ImageMagick.
cd "$(dirname "$0")"
convert -size 640x640 radial-gradient:'#2a475e'-'#171a21' \
  \( -background none -density 96 bot-icon.svg \) -composite -quality 92 bot-icon.jpg
