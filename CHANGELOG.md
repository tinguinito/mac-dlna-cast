# Changelog

Formato inspirado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).
Sin versionado formal por tags todavía — cada entrada es una sesión de trabajo.

## [0.5.0] - 2026-07-27

Fase C hasta C2: **primera versión descargable e instalable** para macOS
(Apple Silicon), Windows y Linux — release `v0.5.0` en GitHub con 7
instaladores. Plan y gates en `docs/PLAN-FASE-C.md`; bitácora en
`sesion_2026-07-26_app_nativa_y_release_multiplataforma.md`.

### Agregado
- **`app/`** — shell Tauri 2: ventana con el HUD (servido por el sidecar),
  tray, single-instance, cerrar-ventana-oculta (convención macOS), reabrir
  desde el Dock. Salir por cualquier vía detiene el server.
- **Server como binario** (PyInstaller onefile, `build_server_bin.sh`):
  `hud.html` empaquetado, caches en el dir de datos del SO, `ping`/`arp`
  portables (Windows/Linux/macOS), `os.execv` consciente del modo congelado.
- **CI `build-app`** (GitHub Actions): matriz de 3 SO, instaladores
  adjuntos a release en borrador (publicar = decisión humana). Mac Intel
  pendiente (runners `macos-13` sin asignación; vía Rosetta anotada).
- **Página**: botones de descarga a `releases/latest` + instrucciones
  Gatekeeper/SmartScreen/firewall.
- Flujo git: rama `development` de integración; `main` = publicado.

### Arreglado
- **Server huérfano al cerrar la app Tauri**: matar al bootloader onefile
  de PyInstaller no mata a su hijo real. Watchdog de stdin
  (`DLNA_EXIT_ON_STDIN_EOF=1`): el server se apaga solo si la app muere
  por cualquier vía, incluso crash.
- **Pausa idempotente**: el Samsung devuelve HTTP 500 ante `Pause` estando
  ya en `PAUSED_PLAYBACK` (doble clic al botón del HUD). Ahora se consulta
  `GetTransportInfo` antes de reenviar.
- `RunEvent::Reopen` es solo-macOS (no compilaba en Windows/Linux).

## [0.4.0] - 2026-07-26

Fase B del plan "menos fricción": la primera versión que se usa **sin
terminal** — app nativa de macOS que levanta el servidor con doble clic.
Estrategia inspirada en Handy (Rust+Tauri): app instalable, autoarranque del
flujo completo, cero comandos. La fase C (Tauri + servidor compilado como
sidecar, instalador multiplataforma) queda planificada en la página del
proyecto.

### Agregado
- **`menubar_dlna.py`** — launcher nativo (rumps/PyObjC en venv dedicado
  `.venv-menubar`; el servidor sigue siendo stdlib pura). Ícono 📺 en Dock y
  barra de menú, inicia/detiene/reinicia el servidor, "Elegir carpeta de
  videos…" con diálogo nativo (persiste en `.dlna_menubar.json`), abre el
  HUD solo al arrancar y con un clic en el Dock.
- **`instalar.sh`** — instalador de un comando: crea el venv, genera el
  bundle "Mac DLNA Cast.app" con rutas de esa copia del repo y lo deja en
  `/Applications` (fallback `~/Applications`).
- **Página del proyecto (GitHub Pages)** — `index.html` ahora es la página
  de producto: qué es, instalación de la beta, cómo se usa, y roadmap
  "Pronto" (app multiplataforma). La narrativa original se movió a
  `historia.html`.

### Arreglado
- **Server huérfano al salir por el Dock/⌘Q.** El "Salir" nativo de macOS no
  pasaba por el ítem de menú propio y el servidor quedaba corriendo sin app.
  Ahora la detención va enganchada a `before_quit` de rumps
  (`applicationWillTerminate`), que cubre Dock, ⌘Q y logout.
- **Ícono fantasma de Python en el Dock** al lanzar como agente: el launcher
  re-ejecuta el `Python.app` del framework y macOS ignora el `LSUIElement`
  del bundle propio. (Terminó siendo irrelevante: se adoptó modo Dock.)
- **Prompt de notificaciones de rumps** anulado (registraba un centro de
  notificaciones que no se usa).

### Aprendido (para la fase C)
- **macOS oculta el status item globalmente** cuando la barra del display
  principal (notch) está llena — sin API para forzarlo, ni siquiera con
  `NSStatusItem Preferred Position` persistido. Verificado por accesibilidad:
  el ítem existe y su menú funciona, pero queda en posición placeholder
  `(-1, …)` y no aparece en ningún monitor. Conclusión de diseño: el tray es
  conveniencia, nunca el único punto de acceso — Dock/ventana primero.

## [0.3.0] - 2026-07-16

Sesión enfocada en confiabilidad de red y control remoto del TV (encendido,
selección entre varias TVs). Todo en `server_dlna.py` + `hud.html`.

### Arreglado
- **`/api/load` mentía éxito.** Casteaba en un hilo de fondo y respondía
  `ok:true` de inmediato sin esperar el resultado real; si el cast fallaba
  (TV apagada, sin red, etc.) el HUD nunca se enteraba. Ahora el cast corre
  síncrono dentro del request y el HUD recibe el error real.
- **Timeout de 6s insuficiente para el primer cast.** La primera vez que se
  castea a una TV nueva, el Samsung muestra un cuadro de "permitir conexión"
  en pantalla y no responde el SOAP hasta que el usuario acepta con el
  control remoto. `tv_set_uri`/`tv_play` ahora usan 25s en ese primer cast
  (los controles normales de play/pausa/seek siguen en 6s).
- **HUD: texto de rutas largas partido letra por letra.** `.lib-root` y
  `.browser-path` tenían `min-width:0` en un flex row junto a otros
  elementos (etiqueta + botón); en viewports angostos se aplastaban a ~20px
  y el texto se partía carácter por carácter. Ahora tienen `min-width`
  fijo, así que si no entran saltan a su propia línea.
- **Choque visual con la barra de estado en iPhone/Safari.** Se agregó
  `viewport-fit=cover` + padding con `env(safe-area-inset-*)` en los 4
  bordes del body.
- **Cast doble → 500 del Samsung.** Dos casts superpuestos (ej. `/api/load`
  seguido de `/api/cast` antes de que el primero terminara) chocaban contra
  el mismo MediaRenderer. Ahora `cast_to_tv()` usa un lock no bloqueante:
  el segundo intento devuelve "ya hay un cast en curso" en vez de romper.

### Agregado
- **Auto-detección de cambio de red.** Un hilo (`network_watch_thread`)
  compara la IP local cada 5s; si detecta una IP nueva estable (dos
  chequeos seguidos, para no reaccionar a un blip de DHCP), el proceso se
  reejecuta solo (`os.execv`) para recuperar SSDP/anuncio limpios. Antes,
  cambiar de banda WiFi (2.4↔5GHz) dejaba el server sirviendo con la IP
  vieja hasta un reinicio manual.
- **Encendido remoto (Wake-on-LAN), verificado en vivo.** `wake_tv()` manda
  el paquete mágico a la MAC cacheada en disco (`.dlna_tv_mac`, persistida
  vía ARP). `cast_to_tv()` lo intenta solo si no encuentra el
  MediaRenderer, y espera hasta 20s a que el TV responda antes de
  rendirse. Botón "⏻ PRENDER TV" + endpoint `/api/wake` para uso manual.
  Confirmado con el Samsung UN55NU7095 real: **sí prende** desde un
  apagado real (no solo standby de red).
- **Selector de TVs disponibles.** `list_renderers()` escanea toda la
  subred por MediaRenderers (no se detiene en el primero) y trae nombre +
  IP + si responde ping. `select_renderer(ip)` fija a mano cuál TV usa el
  HUD (persiste en `.dlna_tv_ip`; una vez fijada, `discover_renderer()` no
  cae de vuelta a otra distinta si falla). Endpoints `/api/tvs` y
  `/api/tvs/select?ip=`. Botón "📺 TV: —" en el HUD con panel de lista.
- **Cliente WebSocket mínimo (`_WSConn`) para el canal de control remoto
  de Samsung** (`ws://<ip>:8001` / `wss://<ip>:8002`,
  `/api/v2/channel/samsung.remote.control`), escrito a mano con stdlib
  (handshake RFC 6455 + framing) para no sumar una dependencia.
  `tv_power_off()` lo usa para mandar `KEY_POWER`. **Probado en vivo contra
  el Samsung real: el canal conecta bien, pero el TV nunca entrega un
  token de autorización ni muestra el cuadro de permiso — rechaza el
  comando con `"unrecognized method value"`.** Es un ajuste del lado del
  TV (Ajustes → General → Administrador de dispositivos externos →
  Administrador de conexión de dispositivos), no un bug del cliente.
  Queda el código y el botón "⏻ APAGAR TV" listos para cuando/si ese ajuste
  se revisa; hoy no apaga.

## [0.2.1] - 2026-07-12
- `.gitignore`: excluir perfil de estilo de decisión personal del repo público.

## [0.2.0] - 2026-07-12
- **Auto-detección del TV.** `discover_renderer()` escanea `:9197/dmr` en
  paralelo por toda la subred y cachea la IP hallada en `.dlna_tv_ip`.
  `DLNA_TV_IP` pasa de requisito a atajo opcional.

## [0.1.1] - 2026-07-12
- HUD: "FALLA DEL REACTOR" como mensaje de error (idea del hijo de César).

## [0.1.0] - 2026-07-12
- Primer release: servidor DLNA/UPnP en Python puro (sin dependencias) +
  HUD web estilo Iron Man para monitorear/controlar la reproducción
  (play/pausa/seek, biblioteca) vía AVTransport.
