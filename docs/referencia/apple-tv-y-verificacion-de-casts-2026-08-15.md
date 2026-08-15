# Referencia — Apple TV como destino y cómo verificar dos casts simultáneos (2026-08-15)

- **Disparador:** "¿cómo transmito películas a la Apple TV? A los TV puedo, ¿pero con las Apple TV cómo sería?",
  seguido de un caso real: mandar una película a una Apple TV y otra, en paralelo, al Samsung por DLNA.
- **Relacionado:** `CLAUDE.md` (arquitectura DMS/DMC, catálogo del Samsung), `README.md` §8.

## 1. La Apple TV no habla DLNA/UPnP

El modelo "MARK II" de este proyecto (push por SOAP AVTransport contra `:9197/dmr`) **no aplica**. tvOS
usa AirPlay, otro protocolo. Tres caminos, de menor a mayor trabajo:

| Vía | Qué requiere | Qué se pierde |
|---|---|---|
| **App cliente DLNA en el Apple TV** (VLC for tvOS, Infuse, nPlayer) | Nada del lado del server: el DMS actual ya sirve por SSDP + `/media/0` | El control del HUD: play/pausa/seek/posición los tiene la app en pantalla |
| **AirPlay manual desde el Mac** (QuickTime → ícono AirPlay) | Nada | El HUD por completo |
| **Portar el control con `pyatv`** | Dependencias + emparejamiento con PIN | Nada, pero rompe la regla de stdlib pura |

### Equivalencias si algún día se implementa `pyatv`

| Concepto del proyecto | Samsung (SOAP) | Apple TV (`pyatv`) |
|---|---|---|
| Iniciar reproducción | `SetAVTransportURI` + `Play` | `play_url(http://<ip-mac>:8200/media/0)` |
| `/api/tvpos` | `GetPositionInfo` | `playing()` → `position`, `total_time`, `device_state` |
| Controles del HUD | `Play`/`Pause`/`Seek`/`SetVolume` | `play`, `pause`, `stop`, `set_position`, `set_volume` |
| `discover_renderer()` | M-SEARCH / escaneo de subred | `atvremote scan` (Bonjour `_airplay._tcp`) |

**Dos advertencias antes de meterse:**

1. **Rompe "stdlib pura".** `pyatv` arrastra `aiohttp`, `cryptography` y más, y es asyncio, mientras el
   server es de hilos bloqueantes. Habría que aislarlo (proceso aparte o hilo con su propio event loop)
   y hacerlo opcional: sin `pyatv` instalado, la Apple TV simplemente no aparece como destino.
2. **El emparejamiento es obligatorio, no opcional.** Verificado en vivo: la Apple TV responde en
   `:7000` pero devuelve **HTTP 403** en `/playback-info` y cuerpo vacío en `/server-info` sin
   credenciales. Es el análogo del cuadro de "permitir conexión" del Samsung, pero acá hay que hacer
   `atvremote pair` con PIN en pantalla y **persistir las credenciales en disco**.

Si se implementa, conviene diseñarlo como una capa de **backends de destino**
(`SamsungDLNA` / `AppleTVAirPlay`) detrás de la interfaz que ya consumen
`/api/cast|play|pause|seek|tvpos`, en vez de meter condicionales de AirPlay dentro de `cast_to_tv()`.

## 2. Cómo verificar que un cast está realmente corriendo (lo no obvio)

Al validar dos transmisiones en paralelo (una por AirPlay, otra por DLNA), **las herramientas
habituales mienten sobre AirPlay**. Verificado en esta sesión:

| Herramienta | Ve el DLNA | Ve el AirPlay | Por qué |
|---|---|---|---|
| `lsof -nP -iTCP@<ip>` | Sí | **No** | El socket lo tiene `AirPlayXPCHelper`, que corre como root; sin `sudo` no aparece |
| `netstat -an -p tcp \| grep <ip>` | Sí | **No** | Mismo motivo |
| `curl http://<appletv>:7000/playback-info` | n/a | **No** | 403: tvOS exige emparejamiento |
| `nettop -P -x -J bytes_out -l 1` (dos tomas, delta) | Sí | **Sí** | Único método que atribuyó el tráfico por proceso |

**Receta que funcionó** (delta de bytes salientes por proceso, ~10 s):

```bash
snap(){ nettop -P -x -J bytes_out -l 1 2>/dev/null | tail -n +2 | awk 'NF==3{print $2, $3}'; }
snap > /tmp/s1.txt; sleep 10; snap > /tmp/s2.txt
# comparar ambos archivos: la diferencia por proceso es el caudal real
```

Resultado del caso real: `AirPlayXPCHelper` 4.6 Mbps + `mac-dlna-cast-server` 4.1 Mbps = **8.7 Mbps**
totales. Nada para un enlace de 866 Mbps.

**Dato lateral útil:** QuickTime recodifica en vivo para AirPlay. Un archivo de 2.3 Mbps salió a
4.6 Mbps. Cuesta CPU al Mac, así que no conviene dormirlo ni cerrar la tapa durante la reproducción.

## 3. Trampa de diagnóstico: identificar la interfaz de red

**Error cometido y corregido en esta sesión.** Se concluyó "el Mac está por Ethernet" a partir de:

```bash
networksetup -listallhardwareports | grep -A2 -i "en0"
# Device: en0
# Ethernet Address: f4:d4:...
```

Dos lecturas equivocadas, ambas evitables:

1. **El `grep -A2` recortó la línea que identificaba el puerto.** `Hardware Port: Wi-Fi` va *arriba* de
   `Device: en0`, no abajo. Con `-A2` se pierde. Y `Ethernet Address:` es sólo la etiqueta de la MAC:
   macOS la usa igual para Wi-Fi. **No** significa que la interfaz sea cableada.
2. **`networksetup -getairportnetwork en0` está deprecado y miente.** Devuelve
   "You are not associated with an AirPort network" aunque la Wi-Fi esté conectada y funcionando.

**Comandos correctos:**

```bash
route -n get default | grep interface          # qué interfaz está activa
networksetup -listallhardwareports             # SIN grep, o con: grep -B2 "Device: en0"
system_profiler SPAirPortDataType | grep -E "PHY Mode|Channel|Signal"   # banda y canal reales
```

En este Mac: `en0` es **Wi-Fi**, canal 44 (5 GHz, 80 MHz), 866 Mbps, señal -48 dBm. Que es exactamente
la configuración que `CLAUDE.md` documenta como la solución a los cortes: **el Mac en 5 GHz, el Samsung
en su 2.4 GHz**, sin competencia de airtime entre ambos tramos.

Regla general: cuando el dato importa, leer el bloque completo o anclar en el campo correcto, y
contrastar con una segunda fuente independiente. Dos indicios débiles no son una medición.

## Pendientes

- `pyatv` como backend opcional para controlar la Apple TV desde el HUD: **no implementado**, sólo
  evaluado. El primer paso obligatorio sería el pairing con PIN.

## Referencias

- `server_dlna.py` — `discover_renderer()`, `cast_to_tv()`, `tv_*()`.
- `CLAUDE.md` — sección "Decisiones y trampas clave", nota de 5 GHz vs 2.4 GHz.
