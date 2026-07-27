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

### C0 · Cimientos — `pendiente`
- Portabilidad de `server_dlna.py`: `ping`/`arp` por SO (Windows: `ping -n -w`,
  `arp -a`; Linux: `ping -c -W`). Superficie ya auditada: solo esas 2 llamadas
  + `ffmpeg/ffprobe` opcionales (degradan con gracia si faltan).
- PyInstaller del server en Mac; probar el binario suelto contra el TV.
- Estructura `app/` (proyecto Tauri) en este mismo repo.
- **Gate:** el binario compilado sirve video al TV igual que el script.

### C1 · App Mac — `pendiente`
- Ventana Tauri cargando `http://127.0.0.1:8200/hud`.
- Gestión del sidecar (spawn al abrir, kill en cualquier vía de salida).
- Tray + Dock, single-instance, quit limpio.
- **Gate:** QA en vivo de César — reemplaza a la app fase B sin perder nada.

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
