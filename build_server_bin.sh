#!/bin/bash
# Compila server_dlna.py a un binario autónomo (PyInstaller onefile).
# Salida: build/dist/mac-dlna-cast-server (con hud.html empaquetado adentro).
# Lo usará la app Tauri como sidecar (fase C) y la CI para los 3 SO.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$DIR/.venv-build/bin/pyinstaller" ] && [ ! -x "$DIR/.venv-build/Scripts/pyinstaller.exe" ]; then
    echo "==> Creando venv de build e instalando PyInstaller…"
    python3 -m venv "$DIR/.venv-build"
    "$DIR/.venv-build/bin/pip" -q install pyinstaller
fi

PYI="$DIR/.venv-build/bin/pyinstaller"
[ -x "$PYI" ] || PYI="$DIR/.venv-build/Scripts/pyinstaller.exe"

# --add-data es relativo al specpath (build/), por eso ../hud.html
"$PYI" --onefile --name mac-dlna-cast-server \
    --add-data "../hud.html:." \
    --distpath "$DIR/build/dist" --workpath "$DIR/build/work" \
    --specpath "$DIR/build" \
    "$DIR/server_dlna.py"

echo
echo "Binario listo: $DIR/build/dist/mac-dlna-cast-server"
