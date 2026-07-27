# Plan Fase C — app multiplataforma "descargar y listo"

> Estado: **aprobado 2026-07-27**. Fase B (app de Mac con rumps) validada en vivo y publicada.
> Este plan es la fuente de verdad de la fase C; actualizar estados aquí al cerrar cada etapa.

## Objetivo

Que cualquier persona baje un instalador desde la página del proyecto
(macOS `.dmg`, Windows `.exe`, Linux AppImage/deb), lo instale como cualquier
app, y al abrirla vea el HUD en su propia ventana. Sin Python, sin terminal,
sin clonar el repo.

## Arquitectura decidida

**Tauri 2 + servidor Python como sidecar** (PyInstaller compila
`server_dlna.py` a binario por plataforma; Tauri lo lanza/mata con la app).

Tradeoff aceptado: ~15–25 MB extra de instalador y dos runtimes, a cambio de
**cero riesgo de regresión** del motor DLNA ya validado (SSDP, byte-range,
SOAP, quirks del Samsung). Reescritura en Rust queda como optimización futura
opcional, no como requisito.

Qué aporta Tauri (estrategia copiada de Handy, con la lección de fase B):

- Ventana nativa con el HUD → se cierra con la app (adiós pestaña huérfana).
- Tray **y** Dock/barra de tareas — el tray es conveniencia, nunca el único
  acceso (aprendido en fase B: macOS oculta status items con la barra llena).
- `single-instance`, `autostart` (opcional, off por defecto), `updater`
  contra GitHub Releases.
- Instaladores de 3 plataformas vía GitHub Actions (`tauri-action`).

## Decisiones tomadas (2026-07-27)

| Decisión | Resolución |
|---|---|
| Firma Apple (US$99/año) | **Sin firma por ahora.** La página instruye clic derecho → Abrir (Gatekeeper). Se firma si el proyecto tracciona. |
| QA Windows/Linux | **Linux: hardware de César. Windows: Álvaro**, descargando desde los links del sitio. |
| Nombre multiplataforma | **Renombrar recién en C4**, justo antes del lanzamiento. Hasta entonces sigue "Mac DLNA Cast". |

## Etapas y gates

### C0 · Cimientos — `hecha (2026-07-27)`
- ✅ Portabilidad de `server_dlna.py`: `ping`/`arp` por SO, caches del binario
  en el dir de datos del SO (`~/Library/Application Support/MacDLNACast` /
  `%APPDATA%` / `~/.local/share`), `hud.html` empaquetado (`_MEIPASS`),
  `os.execv` consciente del modo congelado. `ffmpeg/ffprobe` siguen opcionales.
- ✅ `build_server_bin.sh` → binario onefile de 8.4 MB (PyInstaller 6.21).
- ✅ Estructura `app/` (Tauri 2, template vanilla + bun); `cargo check` limpio.
- ✅ **Gate aprobado:** el binario casteó al TV real — estado `PLAYING`
  reportado por el televisor con posición avanzando, 7.4 MB servidos.
- Nota: el binario parte con caches vírgenes; si el TV está apagado del todo
  en el primer uso, no hay MAC para Wake-on-LAN hasta haberlo visto una vez
  encendido (igual que un usuario nuevo del script).

### C1 · App Mac — `hecha (2026-07-27)`
- ✅ Ventana Tauri con página de carga → HUD real (sidecar en :8200).
- ✅ Sidecar gestionado + **watchdog de stdin** en el server
  (`DLNA_EXIT_ON_STDIN_EOF=1`): se apaga solo si la app muere por cualquier
  vía — matar al bootloader onefile de PyInstaller no alcanza a su hijo real.
- ✅ Tray ("Abrir panel"/"Salir"), Dock, single-instance; cerrar ventana solo
  la oculta (convención macOS), quit real detiene todo.
- ✅ **Gate aprobado:** QA en vivo de César — cast al TV desde la ventana con
  posición real avanzando, tray y Dock visibles.

### C2 · Multiplataforma — `pendiente`
- GitHub Actions: matriz macOS/Windows/Linux, artefactos de instalador.
- Botones de **descarga directa en la página** (apuntando a GitHub Releases).
- Manejo de prompts de firewall (macOS/Windows) documentado en la página.
- **Gate:** Álvaro instala en Windows real desde el link del sitio y el TV
  reproduce; César igual en Linux.

### C3 · Pulido — `pendiente`
- Autostart opcional, updater activo, primer Release público etiquetado.
- Instrucción Gatekeeper/SmartScreen visible junto a los botones de descarga.
- **Gate:** descarga e instalación limpia en una máquina ajena al desarrollo.

### C4 · Lanzamiento — `pendiente`
- Decidir **nombre definitivo** y renombrar (repo, página, app).
- Página: "descargar y listo" reemplaza a "clonar el repo".
- Post 3 de la serie en LinkedIn/redes + Reddit r/selfhosted / Show HN.

## Riesgos conocidos

1. **Firewall**: macOS y Windows preguntan "¿permitir conexiones entrantes?"
   al primer arranque (el server escucha en 0.0.0.0:8200 + SSDP multicast).
   Guiar al usuario en la página y/o en el primer arranque.
2. **Sin firma**: Gatekeeper (Mac) y SmartScreen (Windows) muestran fricción
   de "app no verificada" — mitigada con instrucciones, eliminada si se firma.
3. **SSDP en redes ajenas**: routers que no reenvían multicast entre bandas
   (ya mitigado con autodiscover por escaneo de subred, fase A).
4. **PyInstaller y antivirus Windows**: falsos positivos conocidos; si pega,
   evaluar Nuitka o firma de código Windows.
