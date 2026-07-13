# Mac DLNA Cast — Servidor DLNA + HUD de control para Samsung TV

Reproduce videos locales de tu Mac en un televisor Samsung por WiFi, **sin AirPlay
ni cables**, con un panel de control (HUD) estilo Iron Man para manejar la
reproducción (play/pausa/seek/volumen) desde el navegador.

- **Origen:** MacBook Pro M1 (macOS)
- **Destino:** Samsung **UN55NU7095** (serie NU7100, 2018) — WiFi solo 2.4 GHz
- **Archivos:** MP4 H.264 + AAC (formato nativo del TV, sin transcodificar)

---

## 1. Requisitos

- **Python 3** (ya viene en macOS / o `brew install python`). Sin librerías extra.
- **ffmpeg** (opcional, solo para `--start`): `brew install ffmpeg`. Ya lo tienes.
- Mac y TV en la **misma red**. El Mac **en 5 GHz** (ver §6 — evita cortes).

---

## 2. Cómo levantar el server (3 formas)

Desde la carpeta del proyecto (`/Users/tu-usuario/my_bucket/server_samsung`):

### a) Una película
```bash
python3 server_dlna.py "/Users/tu-usuario/Movies/Mi.Pelicula.1080p/Mi.Pelicula.1080p.mp4"
```

### b) Una película arrancando en un minuto puntual
```bash
python3 server_dlna.py "/ruta/pelicula.mp4" --start 22:00
```
`--start` acepta `MM:SS`, `HH:MM:SS` o segundos. Recorta con ffmpeg (unos
segundos, una sola vez) y arranca justo ahí.

### c) Una CARPETA como biblioteca (recomendado)
```bash
python3 server_dlna.py ~/Movies
```
Escanea todos los videos de la carpeta (y subcarpetas) y los deja
**elegibles desde el HUD**. Sirve el primero al arrancar; desde el navegador
eliges y reproduces cualquiera.

> **El TV se detecta solo.** El server escanea la red, encuentra tu televisor
> (probando el puerto del MediaRenderer en cada IP, en paralelo) y **recuerda su
> IP** para la próxima. Si quieres forzarlo o acelerar el arranque en frío,
> pásale la IP como atajo opcional:
> `DLNA_TV_IP=192.168.1.50 python3 server_dlna.py ~/Movies`.

> **Truco:** para dejarlo corriendo aunque cierres la terminal, antepón `nohup` y
> añade `&` al final:
> `nohup python3 server_dlna.py ~/Movies > server.log 2>&1 &`

**Variables opcionales:**
- `DLNA_PORT=8201 python3 …` → cambia el puerto HTTP (default 8200).
- `DLNA_NO_SSDP=1 python3 …` → no se anuncia por SSDP (solo pruebas).

Al arrancar, la consola muestra la IP, el puerto y la URL del HUD.

---

## 3. Ver en el TV

En el televisor: **Source / Fuente → "Mac DLNA Cast" → abrir → reproducir**.

Si no aparece: sal y vuelve a entrar a *Source* (el TV re-descubre el server), o
verifica que Mac y TV estén en la misma red.

---

## 4. El HUD (panel de control) — modo MARK II

Abre en el navegador del Mac:
```
http://<IP-del-Mac>:8200/hud
```
(La IP la ves en la consola al arrancar. Ejemplo actual: `http://192.168.1.42:8200/hud`.)

El HUD muestra en vivo y **controla el TV**.

**Ya funcionando:**
- **Reactor + gráfico de transferencia** (throughput real Mbps, últimos 60 s).
- **Posición real del TV** (tiempo absoluto, leído del propio televisor).
- **CONTROL · MARK II:** ▶ play, ⏸ pausa, ⏹ stop, **scrubber clickeable**
  (salta a donde toques), saltos rápidos ±10/±30 s, "IR A MM:SS", "⧉ Iniciar en TV".
- **Señal vital del TV:** ping/latencia, IP, MAC, modelo.

**Ya en el server, falta el panel en el HUD (pendiente):**
- **Biblioteca:** elegir y reproducir cualquier video (endpoints `/api/library`,
  `/api/load` listos; requiere arrancar con una carpeta — forma 2c).
- **Volumen/mute:** endpoints `/api/volume`, `/api/mute` listos.

**Detener el server:** `Ctrl+C` en la terminal.

---

## 5. Solución de problemas

| Síntoma | Causa / arreglo |
|---|---|
| **Reloadings / se recarga seguido** | Airtime WiFi. Pon el **Mac en 5 GHz** (§6). Fue *la* causa raíz. |
| No aparece en *Source* | Mac y TV en distinta red, o falta re-descubrir: reentra a *Source*; reinicia el server. |
| "Mac DLNA Cast" duplicado | Ícono fantasma de un arranque previo; el TV lo limpia en ~30 min. Solo el vivo responde. |
| El seek tarda unos segundos | Normal: el TV re-buffera desde la nueva posición. |
| El volumen no cambia | Si usas barra de sonido por HDMI-ARC, el volumen UPnP no la controla (controla los parlantes del TV). |
| Control no responde | El TV debe estar en modo controlado: pulsa "⧉ Iniciar en TV" en el HUD. |

---

## 6. Red: WiFi, cable y hotspot (y sus límites)

**Regla de oro:** funciona en **cualquier red donde el Mac y el TV se vean entre
sí** (misma LAN, sin aislamiento de clientes). El video **no sale a internet** —
viaja Mac → router → TV dentro de tu red.

| Montaje | ¿Funciona? | Notas |
|---|---|---|
| **Mac 5 GHz + TV 2.4 GHz (mismo router)** | ✅ **Recomendado** | El Mac y mi tráfico salen del carril 2.4; el TV queda solo en él. Cero cortes. |
| **Mac por cable Ethernet** | ✅ Lo mejor | Saca al Mac del aire por completo. Ideal si tienes adaptador USB-C→Ethernet. |
| **Hotspot desde el Mac** | ⚠️ Sí, con matiz | El Mac se vuelve el "router" y el TV se conecta a él. Sirve para castear (no necesita internet). Pero el Mac **no puede** usar su WiFi como hotspot *y* para internet a la vez: necesitaría internet por cable. Red dedicada y limpia si solo quieres ver. |
| **Hotspot desde el iPhone** | ⚠️ Depende | Mac y TV se conectan al iPhone. Funciona **solo si el hotspot NO aísla los clientes** (muchos hotspots móviles los aíslan → el TV y el Mac no se verían y falla). El stream local **no gasta datos móviles**. El TV se conecta en 2.4 GHz. |
| **Red guest / con "client isolation"** | ❌ No | El aislamiento bloquea que el TV vea al Mac. |

**Límites técnicos:** requiere misma subred, multicast SSDP permitido y sin
aislamiento de clientes (AP isolation). Redes de invitados y algunos hotspots lo
bloquean.

---

## 7. Enviar a varias TVs (multi-renderer)

El server es **DLNA/UPnP estándar y genérico**: cualquier dispositivo con
"MediaRenderer" en la red lo ve (otra smart TV, consola, etc.). Hoy el control
toma **el primer renderer que responde** (tu Samsung). Para elegir el destino o
mandar a **varias TVs a la vez** habría que añadir un **selector de renderer**
(descubrir todos, elegir/os). No está construido aún — pendiente cuando lo pidas.

---

## 8. Anexo — Todo lo que tu TV puede hacer (catálogo UPnP)

Tu **Samsung UN55NU7095** expone dos dispositivos en la red:

### MediaRenderer — `http://192.168.1.50:9197/dmr`
Es el que usamos para el control de reproducción.

**AVTransport** (reproducción):
- `Play`, `Pause`, `Stop`, `Seek`, `Next`, `Previous`
- `SetAVTransportURI` (qué reproducir), `SetNextAVTransportURI` (**encolar la
  siguiente** → habilitaría autoplay/playlist)
- `GetPositionInfo` (tiempo actual), `GetTransportInfo` (estado), `GetMediaInfo`
- `X_DLNA_GetBytePositionInfo` (posición exacta en bytes), `X_GetStoppedReason`
- `SetPlayMode` (repetir/aleatorio)

**RenderingControl** (imagen y sonido):
- `SetVolume`, `GetVolume`, `SetMute`, `GetMute`
- `X_SetAspectRatio` / `X_GetAspectRatio` (relación de aspecto)
- `X_SetZoom` (zoom a una región), `X_Move360View`/`X_Zoom360View` (video 360°)
- `X_ControlCaption` / `X_GetCaptionState` (**subtítulos**: cargar `.srt`/toggle)
- `X_SetTVSlideShow` (modo presentación de fotos)

**ConnectionManager:** `GetProtocolInfo`, `PrepareForConnection`, etc. (formatos
que el TV acepta).

### DIAL receiver — `http://192.168.1.50:7678/nservice/`
- `SendKeyCode` → **enviar teclas del control remoto** al TV (como un control
  virtual: volumen, navegación, home…). También lanza apps (YouTube/Netflix).

**Ideas que esto habilita a futuro:** autoplay de la biblioteca
(`SetNextAVTransportURI`), subtítulos externos (`X_ControlCaption`), control
remoto virtual (`SendKeyCode`), ajuste de aspecto/zoom desde el HUD.

---

*Servidor casero en Python puro (stdlib). Sin dependencias. Ver `server_dlna.py`.*
