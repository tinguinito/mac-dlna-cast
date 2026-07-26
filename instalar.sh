#!/bin/bash
# Instalador de Mac DLNA Cast (fase B — app nativa de prueba).
#
# Qué hace:
#   1. Crea un venv local (.venv-menubar) e instala rumps (barra de menú/Dock).
#   2. Genera el bundle "Mac DLNA Cast.app" apuntando a ESTA copia del repo.
#   3. Lo instala en /Applications (o ~/Applications si no hay permiso).
#
# Uso:  ./instalar.sh
# Requisitos: macOS + python3 (Homebrew o python.org). El servidor en sí no
# tiene dependencias; el venv es solo para el launcher de barra de menú/Dock.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Mac DLNA Cast.app"
APP="$DIR/$APP_NAME"

echo "==> Verificando python3…"
command -v python3 >/dev/null || { echo "ERROR: falta python3. Instálalo con Homebrew: brew install python"; exit 1; }
python3 --version

echo "==> Creando venv del launcher (.venv-menubar) e instalando rumps…"
python3 -m venv "$DIR/.venv-menubar"
"$DIR/.venv-menubar/bin/pip" -q install --upgrade rumps

echo "==> Generando bundle ${APP_NAME}…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Mac DLNA Cast</string>
    <key>CFBundleIdentifier</key><string>cl.trazala.macdlnacast</string>
    <key>CFBundleVersion</key><string>0.4.0</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundlePackageType</key><string>APPL</string>
</dict>
</plist>
EOF
cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
# Generado por instalar.sh — lanza la app con el venv de esta copia del repo.
exec "$DIR/.venv-menubar/bin/python" "$DIR/menubar_dlna.py"
EOF
chmod +x "$APP/Contents/MacOS/launcher"

echo "==> Instalando en /Applications…"
DEST="/Applications"
if ! cp -R "$APP" "$DEST/" 2>/dev/null; then
    DEST="$HOME/Applications"
    mkdir -p "$DEST"
    rm -rf "$DEST/$APP_NAME"
    cp -R "$APP" "$DEST/"
fi

echo
echo "Listo. Instalada en: $DEST/$APP_NAME"
echo "Ábrela con Spotlight (⌘-espacio → \"Mac DLNA\") o doble clic en Finder."
echo "Al abrirse levanta el servidor y abre el HUD solo en tu navegador."
echo "IMPORTANTE: no muevas ni borres esta carpeta del repo — la app corre desde aquí."
