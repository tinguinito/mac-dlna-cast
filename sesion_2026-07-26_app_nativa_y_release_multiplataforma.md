# De script a app descargable — Bitácora de sesión (2026-07-26 → 2026-07-29)

Continuación de `sesion_2026-07-16_red_encendido_selector.md`. Esta sesión
transformó el proyecto: de "script que se levanta por terminal" a **app
instalable publicada para macOS, Windows y Linux (v0.5.0)**, con página de
producto y estrategia de difusión. Detalle técnico por versión en
`CHANGELOG.md` (0.4.0 y 0.5.0); plan y estados en `docs/PLAN-FASE-C.md`.

- **Disparador:** "que este proyecto no dependa de levantar con un script…
  que lo pueda usar cualquier persona". Se analizó la estrategia de Handy
  (Rust+Tauri, repo `wenumapu-handy-rebrand`) y se definió un plan por fases:
  B (app Mac rápida con rumps) → C (Tauri multiplataforma).

## 1. Fase B — app de Mac con rumps (validada y luego superada)

`menubar_dlna.py` + `instalar.sh` + bundle en `/Applications`. Tres hallazgos
que marcaron el diseño posterior:

- **El quit nativo de macOS (Dock/⌘Q) no pasa por los callbacks propios** —
  el server quedaba huérfano. Fix: enganchar la detención a `before_quit`
  de rumps (`applicationWillTerminate`). El mismo bug reapareció con otra
  cara en C1 (ver §3).
- **macOS oculta los status items GLOBALMENTE cuando la barra del display
  principal (notch) está llena.** Verificado por accesibilidad: el ítem
  existía y su menú funcionaba (se enumeró y clickeó por AppleScript), pero
  su posición era placeholder `(-1, 1105)` y no aparecía en ningún monitor.
  Ni la posición preferida persistida (`NSStatusItem Preferred Position`)
  lo rescata. **Conclusión de diseño: el tray es conveniencia, nunca el
  único punto de acceso** → modo Dock.
- El `LSUIElement` del bundle propio se ignora cuando el launcher re-ejecuta
  el `Python.app` del framework; hay que fijar la política de activación en
  runtime.

## 2. Página, marketing y difusión

- GitHub Pages ya existía (raíz de `main`); `index.html` pasó a ser página
  de producto y la narrativa original se preservó en `historia.html`.
- Iteración de copy pedida por César: **sin jerga técnica en la narrativa**
  ("DLNA", "HUD", "byte-range" fuera del cuerpo; solo en chips "para
  curiosos técnicos").
- Skills antigravity usadas: `copywriting` y `launch-strategy` (marco ORB)
  para la estrategia, y `linkedin-content-generator` para el post 2.
  Aprendizajes aplicables al post 3: máximo 5 hashtags, gancho en 2 líneas
  separadas, el post 1 (440 impresiones) no tenía link de destino — el 2 sí.
- Serie de lanzamiento definida: post 1 (historia, ya publicado semanas
  atrás) → post 2 (beta abierta, publicado en esta sesión) → post 3 (C4,
  "descargar y listo").

## 3. Fase C — plan y ejecución C0→C2 en la misma sesión

Plan completo con gates en `docs/PLAN-FASE-C.md`. Decisiones de César:
sin firma Apple por ahora; QA Linux él / Windows Álvaro; renombre recién
en C4. Flujo git nuevo a pedido suyo: rama `development` de integración,
PRs contra ella, promoción a `main` (que publica Pages) al validar.

- **C0 (gate aprobado):** portabilidad `ping`/`arp` por SO, caches del
  binario al dir de datos del SO, `hud.html` empaquetado (`_MEIPASS`),
  binario PyInstaller de 8.4 MB casteando al TV real (PLAYING con posición
  avanzando reportada por el propio TV vía `/api/tvpos`).
- **C1 (gate aprobado con QA en vivo de César):** shell Tauri 2 con ventana
  → HUD, tray, single-instance, cerrar-ventana-oculta. **Bug cazado:**
  matar al bootloader onefile de PyInstaller no mata a su proceso hijo →
  watchdog de stdin en el server (`DLNA_EXIT_ON_STDIN_EOF=1`): si la app
  muere por cualquier vía (incluso crash), el pipe se cierra y el server
  se apaga solo.
- **C2 (instaladores publicados; gate de hardware real PENDIENTE):** CI
  `build-app` (matriz macOS arm + Windows + Linux; Mac Intel soltado —
  runners `macos-13` sin asignación, vía Rosetta anotada en el workflow),
  release **v0.5.0 publicado** con 7 instaladores y notas para testers,
  página con botones de descarga verificados en vivo
  (`releases/latest` → v0.5.0).

## 4. Bugs de QA cazados durante el uso real

- **Pausa sobre pausa → HTTP 500 del Samsung.** Reproducido por curl:
  `Pause` en estado `PAUSED_PLAYBACK` es rechazado por el renderer. Fix:
  `tv_pause()` consulta `GetTransportInfo` primero y no reenvía si ya está
  pausado (commit en v0.5.0). Nota: verificado el diagnóstico en vivo; el
  fix quedó compilado en el release, pendiente de re-probar en uso real.
- **Botón "Apagar TV" no apaga:** NO es bug nuevo — es la limitación
  documentada en `CLAUDE.md` (el TV no entrega token por el canal
  `ms.remote.control`). Queda pendiente que César revise el menú del TV
  (Administrador de dispositivos externos) o marcar el botón como "no
  disponible en este TV".

## 5. Pendientes

| Qué | Quién | Estado |
|---|---|---|
| Gate C2: instalar desde el sitio en Windows (Álvaro) y Linux (César), TV reproduce | Álvaro + César | Pendiente |
| C3: autostart, updater, gate "máquina ajena" | Claude + César | Pendiente |
| C4: renombre definitivo, página "descargar" como método principal, post 3, Reddit/HN | César decide nombre | Pendiente |
| Build Mac Intel (vía Rosetta en runner arm) | Claude | Anotado en workflow |
| Re-verificar fix de pausa doble en uso real (v0.5.0) | César | Pendiente |
| Botón "Apagar TV": revisar menú del TV o marcarlo no disponible | César primero | Pendiente |

## 6. Referencias

- `docs/PLAN-FASE-C.md` (plan, decisiones y estados) · `CHANGELOG.md` 0.4.0/0.5.0
- PRs: #1 (fase B), #2 (C0), #3 (C1), #4 (C2) · Release: tag `v0.5.0`
- Página: https://tinguinito.github.io/mac-dlna-cast/ · Descargas: `releases/latest`
- Nota fuera de este repo: la decisión Mark I/tmux de la sesión quedó en
  `agente-personal/PENDIENTES.md` (P1).
