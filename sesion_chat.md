# Reproducir video local en Samsung NU7095 por WiFi — Bitácora de sesión

**Objetivo:** reproducir un `.mp4` local (Iron Man 2008) desde un MacBook Pro M1 en un
televisor Samsung, sobre red WiFi Starlink, sin cables.

**Resultado:** AirPlay descartado (bug conocido QuickTime+Samsung + limitación de VLC).
Solución construida: **servidor DLNA propio en Python** (`dlna_cast.py`). Pendiente de
validar en el TV.

---

## 1. Entorno

| Elemento | Detalle |
|---|---|
| Origen | MacBook Pro M1 ("MacBook Pro de Cesar"), macOS actual, QuickTime Player |
| Destino | Samsung **UN55NU7095** (serie NU7100, 2018, 55") |
| Firmware TV | `T-KTSUUABC-1360.0` / `VS: 1360.260402` (al día) |
| MAC del TV | `AA:BB:CC:DD:EE:FF` · Región `CHILE_DTV` |
| Red | WiFi Starlink, banda 2.4 GHz, ambos dispositivos en la misma red |

### Archivo objetivo (ffprobe)

```
Contenedor : MP4 (M4V/isom/avc1)
Duración   : 02:06:01, bitrate ~2202 kb/s
Video      : H.264 High, yuv420p, 1920x800 (DAR 12:5), 23.98 fps, ~1899 kb/s
Audio #1   : AAC LC, 48 kHz, estéreo, 164 kb/s  [spa/latino, default]
Audio #2   : AAC LC, 48 kHz, estéreo, 133 kb/s  [eng]
Subtítulo  : mov_text (tx3g) [spa]
```

**Conclusión sobre el archivo:** formato ideal y nativo para el TV (H.264 + AAC).
**No requiere transcodificación.** Es la clave que hace viable el enfoque DLNA.

---

## 2. Diagnóstico (qué se probó y qué se descartó)

El valor de esta sesión está en el proceso de eliminación. Orden real:

1. **Hipótesis inicial errónea:** "QuickTime no castea". → **Falso.** QuickTime sí tiene
   AirPlay y el TV aparecía en la lista de destinos.

2. **Aislamiento de clientes / mDNS bloqueado en Starlink.** → **Descartado.** Si el TV
   aparece en la lista de AirPlay, el descubrimiento (mDNS/Bonjour) funciona y los
   dispositivos se ven en la LAN.

3. **Compatibilidad del TV con AirPlay 2.** → **Confirmada.** La serie NU7100 (2018) está
   en la lista oficial de Apple/Samsung; el soporte llegó por firmware y el del equipo
   está actualizado. No falta nada por ese lado.

4. **Formato del archivo.** → **Descartado como problema.** H.264 + AAC es justo lo que
   AirPlay y el TV decodifican por hardware.

5. **Handshake / emparejamiento.** Se hizo *Reset Paired Devices* y ajustes de
   *Require Code*. → El handshake **sí conecta**: QuickTime mostró "Este video se está
   reproduciendo en Samsung 7 Series (55)" y el iPhone confirmó "AirPlay ya está ocupado"
   (o sea, el MacBook tomó la sesión).

6. **Síntoma central:** conecta pero **no llega el stream**. Pantalla de AirPlay fija en
   el TV, sin audio ni video, y el contador de tiempo en QuickTime **se congela** (el
   playhead refleja lo que reporta el TV; si el buffer nunca se llena, queda pegado).

7. **Firewall de macOS** (teoría fuerte: bloqueaba la conexión entrante del TV al Mac).
   → **Descartado.** El firewall **estaba desactivado** ("se permiten todas las
   conexiones entrantes").

8. **Prueba discriminante — mirroring vs. video:**
   - **Mirroring (duplicar pantalla) SÍ funciona**, desde iPhone y desde MacBook.
   - **AirPlay de video de QuickTime NO.**
   - El mirroring pesa *más* que el archivo (~2 Mbps), así que **no es ancho de banda**
     ni red.

### Causa raíz

Es el **bug conocido de QuickTime + Samsung con el AirPlay de video de archivo**
(no-mirroring). Ampliamente reportado en la comunidad Samsung con estos mismos síntomas:
mirroring OK, iPhone OK, pero el video vía QuickTime solo muestra la pantalla de conexión
y nunca arranca. Dato relevante del mismo hilo: **AirPlay de video desde Safari (YouTube)
sí funciona** → el receptor de video del TV prefiere HLS (m3u8 segmentado) sobre el MP4
progresivo que sirve QuickTime.

---

## 3. Descarte de VLC

Se intentó VLC como emisor alternativo (menú **Reproducción → Procesador**, que es el
"Renderer" traducido).

- El submenú solo mostró **"Sin representador"**: el Samsung **no aparece**.
- Motivo: el *Renderer* de VLC en macOS está pensado para **Chromecast**, no AirPlay; y su
  soporte de AirPlay es parcial (problema típico "solo audio"). El NU7100 no habla
  Chromecast, así que VLC no tiene forma de enviarle el video.
- (Ojo: *Vídeo → Dispositivo de vídeo a pantalla completa* NO es AirPlay; solo elige el
  monitor físico.)

**Conclusión:** ni QuickTime ni VLC sirven para este TV con este archivo.

---

## 4. Solución construida — Servidor DLNA propio (`dlna_cast.py`)

**Idea:** no pelear con AirPlay (protocolo cerrado). Montar un **MediaServer DLNA** en el
Mac. El TV lo ve en *Source / Fuente* y reproduce el archivo con su **reproductor nativo**
— igual que un pendrive USB pero por WiFi. Sin transcodificar (MP4 ya es H.264+AAC).

**Viabilidad:** aunque Samsung "oficialmente" dice que el NU7100 no soporta servidores
DLNA, varios usuarios del mismo modelo confirman que sí funciona vía *Source*.

### Características técnicas del script

- **Python puro (stdlib), sin dependencias.** No requiere `pip install`.
- **SSDP** (UDP multicast 239.255.255.250:1900): responde a `M-SEARCH` y envía `NOTIFY
  ssdp:alive` periódico para que el TV lo descubra.
- **UPnP/DLNA MediaServer:** device description, ContentDirectory (SOAP `Browse`),
  ConnectionManager (`GetProtocolInfo`), y manejo de `SUBSCRIBE`/`UNSUBSCRIBE`.
- **HTTP con byte-range (`206 Partial Content`):** imprescindible para que el Samsung
  haga seek y bufferee. Incluye cabeceras DLNA que el Samsung exige:
  - `contentFeatures.dlna.org: DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=0170...`
  - `transferMode.dlna.org: Streaming`

### Validación ya hecha (en sandbox)

- `desc.xml`, `cd.xml`, `cm.xml` → XML válido.
- SOAP `Browse` (BrowseDirectChildren y BrowseMetadata) → DIDL-Lite correcto con el ítem.
- `HEAD /media/0` → devuelve `Content-Length` + cabeceras DLNA.
- `GET` con `Range: bytes=0-1023` → `206`, `Content-Range` correcto, 1024 bytes exactos.
- `GET` con `Range: bytes=4000000-` (seek abierto) → `206`, tamaño correcto.
- **Pendiente:** descubrimiento SSDP real contra el TV (no testeable en sandbox).

### Uso

```bash
python3 dlna_cast.py "/Users/tu-usuario/Movies/Mi.Pelicula.1080p/Mi.Pelicula.1080p.mp4"
```

En el TV: **Source / Fuente → "Mac DLNA Cast" → abrir carpeta → reproducir**.
Detener: `Ctrl+C`.

### Parámetros clave (en el script)

- `HTTP_PORT = 8200`
- `SSDP_ADDR/PORT = 239.255.255.250:1900`
- `DLNA_PN` = flags DLNA para op de rango.
- MIME por extensión (mp4/m4v→`video/mp4`, mkv→`video/x-matroska`, etc.).

---

## 5. Alternativas / fallbacks (orden de fiabilidad)

1. **Pendrive USB (garantía absoluta).** Copiar el MP4 a un pendrive (FAT32/exFAT) →
   puerto USB del TV → *Source → USB* → reproducir. El TV decodifica H.264+AAC nativo,
   sin red. Es la vía a prueba de todo.
2. **DLNA propio** (`dlna_cast.py`) — la solución inalámbrica de esta sesión.
3. **Apple TV por HDMI** — si se quiere AirPlay estable a futuro (hardware Apple nativo,
   sin el bug de QuickTime).
4. **Plex / Infuse / Jellyfin** — servidor multimedia formal con transcodificación y app;
   más robusto que el DLNA casero si se vuelve un uso frecuente.

---

## 6. Pendientes / próximos pasos (para Code CLI)

- [ ] **Validar `dlna_cast.py` contra el TV real.** ¿Aparece "Mac DLNA Cast" en *Source*?
- [ ] Si no aparece: reiniciar script, reiniciar Smart Hub del TV, verificar que Starlink
      no tenga *client isolation* activo para tráfico multicast (SSDP usa multicast, no
      solo mDNS).
- [ ] Si aparece pero no reproduce: revisar logs HTTP del script (¿el TV hace GET con
      Range?), probar variantes de `protocolInfo`/flags DLNA para perfil Samsung.
- [ ] **Mejora:** manejar subtítulos (el archivo trae `tx3g` spa). DLNA + subs externos
      `.srt` sidecar suele necesitar un `<res>` adicional o quemar subs (obliga a
      reencode). Evaluar si vale la pena.
- [ ] **Mejora:** ruta HLS. Como el receptor AirPlay del TV sí acepta HLS (YouTube via
      Safari funciona), una segunda vía sería: `ffmpeg -c copy -f hls` (remux sin pérdida)
      + servir m3u8 + disparar AirPlay apuntando al m3u8 local. Más complejo (requiere
      hablar el protocolo AirPlay), pero replicaría lo que hace Safari.
- [ ] **Mejora:** auto-detección de múltiples archivos en una carpeta (hoy sirve uno solo).
- [ ] **Mejora:** flag opcional para reencode/transcode on-the-fly si algún archivo no es
      H.264+AAC.

---

## 7. Aprendizajes transferibles

- **Mirroring ≠ AirPlay de video.** Usan caminos distintos; que uno funcione no implica el
  otro. Mirroring = el Mac empuja pantalla; AirPlay de video = el receptor jala el media.
- **El receptor AirPlay de Samsung (2018) es flojo con MP4 progresivo** pero acepta HLS.
- **DLNA sigue siendo la vía universal** para "USB por red" cuando AirPlay/Chromecast
  fallan, siempre que el archivo esté en códec nativo del TV.
- **QuickTime AirPlay de archivo con Samsung = bug recurrente.** No perder tiempo ahí.

---

### Archivos de la sesión

- `dlna_cast.py` — servidor DLNA (Python stdlib).
- `sesion_airplay_dlna_samsung.md` — este documento.