# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Servidor **DLNA/UPnP en Python puro (stdlib, sin dependencias)** que reproduce
videos locales del Mac en un televisor **Samsung UN55NU7095** por WiFi, con un
**HUD web** (estilo Iron Man) para monitorear y controlar la reproducción.

Dos archivos son el proyecto entero:
- `server_dlna.py` — el servidor (todo el backend).
- `hud.html` — el panel de control, servido por el propio server en `/hud`.

`README.md` es el manual de usuario; `sesion_chat.md` es la bitácora de la sesión.

## Comandos

```bash
# Levantar (3 formas)
python3 server_dlna.py "/ruta/pelicula.mp4"              # un archivo
python3 server_dlna.py "/ruta/pelicula.mp4" --start 22:00 # arranca en ese minuto
DLNA_TV_IP=192.168.1.50 python3 server_dlna.py ~/Movies # carpeta = biblioteca (+ hint de IP del TV)

# Verificar sintaxis (no hay build/lint/test formal)
python3 -m py_compile server_dlna.py

# Probar SIN tocar el TV real ni ensuciar la LAN (puerto aislado, sin SSDP)
DLNA_PORT=8201 DLNA_NO_SSDP=1 python3 server_dlna.py "/ruta/dummy.mp4"

# HUD en el navegador
open "http://<IP-del-Mac>:8200/hud"

# Detener: Ctrl+C
```

No hay framework de tests. La validación se hace con `py_compile` + probar
endpoints con `curl` contra una instancia en puerto aislado (`DLNA_NO_SSDP=1`),
y para la lógica pura importando el módulo (`import server_dlna`) y llamando
funciones con `STATE`/`METRICS` seteados a mano.

## Arquitectura

El TV se maneja con **dos modelos que coexisten**:

1. **DMS — MediaServer (pull).** El TV descubre el server por **SSDP** y *jala*
   el archivo por **HTTP con byte-range**. Es el modo "Source → Mac DLNA Cast →
   reproducir". Endpoints: `/desc.xml`, `/cd.xml`, `/cm.xml` (descripción UPnP),
   `/media/0` (stream con `206 Partial Content` + cabeceras DLNA).

2. **DMC — Control (push) "MARK II".** El server le *ordena* al TV vía **SOAP
   AVTransport/RenderingControl** (el TV expone un MediaRenderer en
   `:9197/dmr`). Da tiempo absoluto real y control (play/pausa/seek/volumen)
   desde el HUD. Ver `discover_renderer()` + `tv_*()` + `cast_to_tv()`.

**Hilos** (todos daemon, arrancan en `main`): HTTP server (`ThreadingHTTPServer`),
respondedor SSDP (`ssdp_server`), anunciador SSDP (`ssdp_announcer`),
salud del TV (`tv_health_thread`, ping+ARP).

**Estado global:** `STATE` (archivo servido, IP, tamaño, offset), `METRICS`
(bytes, throughput, `contig_end`) bajo `_metrics_lock`, `TV_CTRL` (URLs de
control cacheadas), `LIBRARY` (videos escaneados).

**HUD ↔ server.** `hud.html` es 100% autocontenido (sin deps externas) y hace
polling a `/stats.json` (throughput, transferencia, salud TV) y a `/api/tvpos`
(posición **real** reportada por el TV). Doble fuente de posición: si el TV está
en control (`/api/tvpos` da estado PLAYING/PAUSED) usa esa como autoritativa; si
no, cae a la estimación de `/stats.json`. Endpoints de control:
`/api/{cast,play,pause,stop,seek,volume,mute,tvpos,library,load}`.

## Decisiones y trampas clave (no obvias)

- **Editar `server_dlna.py` NO afecta al proceso vivo.** Hay que reiniciar
  (`pkill -f server_dlna.py` + relanzar) para aplicar cambios, y **reiniciar
  corta la reproducción** en el TV. En cambio, editar `hud.html` se ve al
  **recargar el navegador** (el server lo lee de disco en cada `GET /hud`), sin
  reiniciar.

- **Seek por AVTransport requiere estado `PLAYING`.** El Samsung devuelve
  HTTP 500 si se le manda `Seek` durante `TRANSITIONING` (mientras carga).
  `cast_to_tv()` espera a PLAYING antes de saltar.

- **Descubrir el renderer por multicast es poco fiable** entre bandas 5↔2.4 GHz
  (el router no siempre reenvía el M-SEARCH). `discover_renderer()` cae a
  **unicast** contra `http://<ip>:9197/dmr`, usando `METRICS["tv_ip"]` (se llena
  cuando el TV pide media) o el hint `DLNA_TV_IP`. Para castear en arranque frío
  (sin que el TV haya pedido nada aún) hace falta `DLNA_TV_IP`.

- **Estimador de posición `contig_end`.** Sin control DMC, la posición se estima
  con el "borde contiguo" de bytes servidos desde 0. NO usar el máximo offset:
  el reproductor lee el índice `moov` del final del MP4 y dispararía la posición
  a 100%. Con DMC activo, la posición real la da el TV (`/api/tvpos`), no la
  estimación.

- **`--start MM:SS`** recorta con `ffmpeg -c copy -movflags +faststart` a un
  temporal `.dlna_seek_<pid>.mp4` junto al original. Se limpia con `atexit` +
  handler `SIGTERM`, y se barren huérfanos previos al arrancar. Sin esto, cada
  reinicio dejaría un archivo de ~1.5 GB.

- **Reloading = airtime WiFi, no CPU.** La causa raíz de los cortes fue el Mac en
  **2.4 GHz** compitiendo airtime con el stream. Solución: **Mac en 5 GHz** (el
  TV es solo 2.4). No perseguir CPU/RAM.

- **UDN fijo** (`uuid5` de namespace estable) para que los reinicios reusen la
  misma identidad y el TV no muestre íconos fantasma duplicados.

- **Pylance** marca muchos "tipos desconocidos": es ruido (código sin
  anotaciones de tipo), no errores. Ignorar.

## El TV (referencia)

Samsung **UN55NU7095**, WiFi **solo 2.4 GHz**. Expone:
- MediaRenderer `:9197/dmr` → **AVTransport** (Play/Pause/Seek/SetURI/
  SetNextAVTransportURI…), **RenderingControl** (Volume/Mute/AspectRatio/Zoom/
  subtítulos `X_ControlCaption`), ConnectionManager.
- DIAL `:7678/nservice/` → `SendKeyCode` (teclas del control remoto), lanzar apps.

El catálogo UPnP completo está en `README.md` §8.
