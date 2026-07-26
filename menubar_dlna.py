#!/usr/bin/env python3
"""App de barra de menú para Mac DLNA Cast.

Fase B del plan "menos fricción": un ícono en la barra de menú de macOS que
levanta/detiene/reinicia el servidor DLNA sin tocar la terminal, y abre el HUD.
Corre con el venv del proyecto (.venv-menubar, tiene rumps); el servidor en sí
sigue siendo stdlib pura y se lanza como subproceso con el python del sistema.

Uso directo:   .venv-menubar/bin/python menubar_dlna.py
Uso normal:    doble clic en "Mac DLNA Cast.app" (bundle generado junto a este archivo)
"""

import json
import os
import signal
import subprocess
import sys
import urllib.request
import webbrowser

import rumps

# Modo Dock: en el Mac del usuario la barra de menú del display principal
# (notch) está llena y macOS oculta el status item globalmente, sin API para
# forzarlo. El Dock nunca se llena, así que la app vive ahí: ícono de TV
# dibujado en runtime (el proceso real es Python.app del framework, no podemos
# cambiar su Info.plist) y clic en el Dock = abrir el HUD. El status item se
# mantiene por si la barra libera espacio. rumps registra un centro de
# notificaciones (pide permiso al usuario) que no usamos — se anula.
from AppKit import (NSApplication, NSImage, NSAttributedString,
                    NSFont, NSFontAttributeName)

rumps.notifications._init_nsapp = lambda nsapp: None


def _set_dock_icon():
    img = NSImage.alloc().initWithSize_((256, 256))
    img.lockFocus()
    text = NSAttributedString.alloc().initWithString_attributes_(
        "📺", {NSFontAttributeName: NSFont.systemFontOfSize_(190.0)})
    text.drawAtPoint_((16, 26))
    img.unlockFocus()
    NSApplication.sharedApplication().setApplicationIconImage_(img)


def _patch_dock_click(open_hud):
    """Clic en el ícono del Dock (reopen) → abrir el HUD."""
    def handler(self, app, has_visible_windows):
        open_hud(None)
        return False
    try:
        rumps.rumps.NSApp.applicationShouldHandleReopen_hasVisibleWindows_ = handler
    except Exception:
        pass

# Los NSStatusItem nuevos entran por la IZQUIERDA de la zona de estado, que es
# lo primero que macOS oculta cuando la barra no cabe (notch). Anclamos una
# posición preferida cerca del reloj (distancia en puntos desde el borde
# derecho) vía autosaveName para que sobreviva a la barra llena.
STATUS_AUTOSAVE = "MacDLNACast"


def _pin_status_item(app):
    try:
        from Foundation import NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
        key = "NSStatusItem Preferred Position " + STATUS_AUTOSAVE
        if defaults.objectForKey_(key) is None:
            defaults.setFloat_forKey_(180.0, key)
        app._nsapp.nsstatusitem.setAutosaveName_(STATUS_AUTOSAVE)
    except Exception:
        pass  # si falla, la app sigue funcionando aunque el ícono quede oculto

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(PROJECT_DIR, "server_dlna.py")
LOG_PATH = os.path.expanduser("~/Library/Logs/MacDLNACast.log")
CONFIG_PATH = os.path.join(PROJECT_DIR, ".dlna_menubar.json")
PORT = int(os.environ.get("DLNA_PORT", "8200"))
DEFAULT_MEDIA = os.path.expanduser("~/Movies")

ICON_ON = "📺"
ICON_OFF = "📴"


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
    except Exception:
        pass


def server_alive():
    """True si hay un server respondiendo en el puerto local."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats.json", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def kill_stray_servers():
    """Mata instancias de server_dlna.py que no lanzamos nosotros."""
    subprocess.run(["pkill", "-f", "server_dlna.py"], capture_output=True)


class DlnaMenuBar(rumps.App):
    def __init__(self):
        super().__init__(ICON_OFF, quit_button=None)
        cfg = load_config()
        self.media_path = cfg.get("media_path", DEFAULT_MEDIA)
        self.proc = None

        self.item_status = rumps.MenuItem("Estado: detenido")
        self.item_status.set_callback(None)
        self.item_hud = rumps.MenuItem("Abrir HUD", callback=self.open_hud)
        self.item_toggle = rumps.MenuItem("Iniciar servidor", callback=self.toggle_server)
        self.item_restart = rumps.MenuItem("Reiniciar servidor", callback=self.restart_server)
        self.item_folder = rumps.MenuItem("Elegir carpeta de videos…", callback=self.choose_folder)
        self.item_folder_now = rumps.MenuItem(f"Carpeta: {self._short(self.media_path)}")
        self.item_folder_now.set_callback(None)
        self.item_quit = rumps.MenuItem("Salir (detiene el servidor)", callback=self.quit_app)

        self.menu = [
            self.item_status,
            self.item_hud,
            None,
            self.item_toggle,
            self.item_restart,
            None,
            self.item_folder_now,
            self.item_folder,
            None,
            self.item_quit,
        ]

        rumps.Timer(self.refresh, 5).start()
        rumps.events.before_start.register(lambda: _pin_status_item(self))
        # Cualquier vía de salida (⌘Q, "Salir" del Dock, logout) pasa por
        # applicationWillTerminate → before_quit: sin esto el server queda
        # huérfano sirviendo al TV con la app ya cerrada.
        rumps.events.before_quit.register(self.stop_server)
        _set_dock_icon()
        _patch_dock_click(self.open_hud)
        self.start_server()
        self._open_hud_when_ready()

    @staticmethod
    def _short(path):
        home = os.path.expanduser("~")
        return path.replace(home, "~")

    # --- ciclo de vida del server ---

    def start_server(self, _=None):
        if server_alive():
            kill_stray_servers()
        logf = open(LOG_PATH, "a")
        self.proc = subprocess.Popen(
            ["/usr/bin/env", "python3", SERVER, self.media_path],
            stdout=logf, stderr=subprocess.STDOUT,
            cwd=PROJECT_DIR, start_new_session=True,
        )
        self.refresh()

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except Exception:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self.proc = None
        kill_stray_servers()
        self.refresh()

    def toggle_server(self, _):
        if self._running():
            self.stop_server()
        else:
            self.start_server()

    def restart_server(self, _):
        self.stop_server()
        self.start_server()

    def _running(self):
        return (self.proc and self.proc.poll() is None) or server_alive()

    # --- acciones de menú ---

    def open_hud(self, _):
        webbrowser.open(f"http://127.0.0.1:{PORT}/hud")

    def _open_hud_when_ready(self):
        """Al lanzar la app, abre el HUD apenas el server responde (máx 15 s)."""
        import threading, time

        def waiter():
            for _ in range(30):
                if server_alive():
                    self.open_hud(None)
                    return
                time.sleep(0.5)

        threading.Thread(target=waiter, daemon=True).start()

    def choose_folder(self, _):
        script = 'POSIX path of (choose folder with prompt "Carpeta de videos para el TV")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        path = r.stdout.strip()
        if r.returncode == 0 and path:
            self.media_path = path.rstrip("/")
            save_config({"media_path": self.media_path})
            self.item_folder_now.title = f"Carpeta: {self._short(self.media_path)}"
            self.restart_server(None)

    def quit_app(self, _):
        self.stop_server()
        rumps.quit_application()

    # --- refresco de estado ---

    def refresh(self, _=None):
        alive = self._running() and server_alive()
        self.title = ICON_ON if alive else ICON_OFF
        self.item_status.title = "Estado: sirviendo al TV" if alive else "Estado: detenido"
        self.item_toggle.title = "Detener servidor" if alive else "Iniciar servidor"


if __name__ == "__main__":
    DlnaMenuBar().run()
