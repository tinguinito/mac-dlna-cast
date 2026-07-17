# Red, encendido remoto y selector de TVs — Bitácora de sesión (2026-07-16)

Continuación de `sesion_chat.md` (esa cubre el diseño original del servidor DLNA;
esta cubre una sesión de uso real posterior, con la app ya funcionando en
producción casera).

## 1. Punto de partida

El HUD ya funcionaba (ver commits `eed838a`..`ab01755`). Esta sesión arrancó por
un reporte de uso real: "levanto el server, busco una película, le doy play y
la TV está apagada, no pasa nada, no envía ningún mensaje. Faltaron cositas."

## 2. Bug encontrado en el flujo de carga

`/api/load` casteaba en un hilo de fondo (`threading.Thread(target=cast_to_tv,
daemon=True).start()`) y respondía `{"ok": true}` de inmediato, sin esperar el
resultado real. El HUD mostraba éxito aunque el cast fallara después, en
silencio. Se corrigió haciendo el cast síncrono dentro del request (ver
CHANGELOG 0.3.0).

## 3. El TV "apagado" en realidad estaba en standby de red

Con el fix de mensajes ya aplicado, un caso real (TV apagada con el control)
mostró que **el TV sí reacciona a la primera llamada SOAP**: se despierta y
muestra un cuadro de "permitir conexión" en pantalla. Una vez aprobado una
vez, el TV lo recuerda para siempre — porque el server usa un UDN fijo
(`uuid5` de un namespace estable), así que reinicios del server no rompen esa
confianza ya otorgada.

Esto separó el problema en dos:
- **Standby de red** (el TV responde pero necesita el cuadro de aprobación):
  ya cubierto por el fix de mensajes + timeout extendido a 25s en el primer
  `SetAVTransportURI`/`Play`.
- **Apagado real** (TV totalmente sin red): requiere otro mecanismo. Ver §5.

## 4. Interludio: cambios de red y cortes de luz

Durante la sesión hubo un cambio real de banda WiFi (2.4→5GHz) y dos cortes de
luz. El cambio de red dejó el server sirviendo con la IP vieja (capturada una
sola vez en `main()`), lo que confirmó en vivo el bug de arquitectura:
"editar el server no lo actualiza, pero tampoco detecta solo un cambio de
IP". Se agregó `network_watch_thread` (auto-restart vía `os.execv` al detectar
IP nueva estable). Los cortes de luz no rompieron nada del lado del código
(el Mac no se apagó); solo cortaron la reproducción al reiniciar el server
para aplicar cada fix, y cada vez se le devolvió la película al segundo exacto
donde había quedado (capturando posición + id de biblioteca antes de
reiniciar).

También apareció una race: un segundo cast disparado mientras el primero
seguía en curso (`/api/load` + `/api/cast` casi simultáneos) hacía que el
Samsung devolviera 500. Se agregó un lock no bloqueante en `cast_to_tv()`.

## 5. Encendido remoto (Wake-on-LAN) — confirmado en vivo

Con la TV apagada de verdad (confirmado con `ping` dando 100% de pérdida), se
mandó un paquete WOL de prueba a la MAC cacheada por ARP
(`5c:c1:d7:74:37:0e`), por los puertos 9 y 7, a broadcast de subred y global.

**Resultado: la TV prendió de verdad** (pantalla encendida, no solo red
respondiendo). Confirmado por el usuario mirando el televisor físicamente.
Se integró a `wake_tv()` + `cast_to_tv()` (intenta WOL si no encuentra el
MediaRenderer, espera hasta 20s) + botón manual en el HUD.

## 6. Selector de TVs disponibles

Pedido explícito: poder elegir a cuál TV castear si hay más de una en la red
(el usuario confirmó que sí hay varias). Se agregó `list_renderers()` (escanea
la subred completa, no se detiene en la primera) y un panel en el HUD para
fijar la TV activa (`select_renderer`).

Al probarlo en el celular apareció el mismo bug de texto vertical que ya se
había arreglado en BIBLIOTECA, pero en la clase CSS compartida
`.browser-path` (la reusé para el panel de TVs sin notar que tenía el mismo
`min-width:0`). Arreglado en el mismo lugar para que no vuelva a aparecer en
ningún otro panel que reuse esa clase.

## 7. Apagado remoto — investigado, NO logrado (limitación del TV, no del código)

Se investigó la vía real de Samsung para apagar: un WebSocket de control
remoto (`ws://<ip>:8001` / `wss://<ip>:8002`,
`/api/v2/channel/samsung.remote.control`), con su propio token de
autorización. Se implementó un cliente WebSocket mínimo a mano (stdlib puro,
sin dependencias nuevas) y se probó en vivo contra el TV real:

- El canal **conecta perfecto** (handshake 101, evento `ms.channel.connect`
  con la lista de clientes).
- **Nunca entrega un token ni muestra el cuadro de permiso en pantalla** —
  se conecta en un modo anónimo/limitado.
- El comando `ms.remote.control` (`KEY_POWER` y también se probó
  `KEY_POWEROFF`) es rechazado con `{"event":"ms.error","data":{"message":
  "unrecognized method value : ms.remote.control"}}`.

Se repitió la prueba pidiéndole al usuario que mirara la pantalla por si el
cuadro de permiso había aparecido sin que se notara — la conexión se aceptó
en 0.0s ambas veces, sin ninguna pausa esperando aprobación humana. Eso
descarta que sea un problema de timing.

**Conclusión: es un ajuste del lado del TV**, probablemente en
`Ajustes → General → Administrador de dispositivos externos → Administrador
de conexión de dispositivos` (la "Notificación de acceso" puede estar
desactivada o en un modo que no muestra el cuadro y limita comandos). El
usuario decidió no seguir investigando ese menú por ahora. El código
(`tv_power_off()`, botón "⏻ APAGAR TV", endpoint `/api/tvoff`) queda
integrado y funcional en su parte de cliente; simplemente no tiene efecto
hoy contra este TV hasta que se revise ese ajuste (o se acepte el cuadro de
permiso si algún día aparece).

## 8. Aprendizajes transferibles

- **"Apagado" de un Smart TV casi nunca es apagado real de la NIC.** El
  standby de red sigue respondiendo SOAP/ping; solo un corte de energía o
  un apagado explícito profundo lo saca de la red de verdad.
- **Wake-on-LAN sí funciona sobre WiFi en este modelo** (Samsung
  UN55NU7095, 2018) — no daba por sentado que un TV solo-WiFi soportara
  WoWLAN, y sí lo soporta.
- **Un canal que "conecta bien" no implica que esté autorizado.** El
  WebSocket de Samsung acepta la conexión igual sin mostrar el cuadro de
  permiso cuando cierto ajuste del TV lo bloquea, y el error que devuelve
  (`"unrecognized method value"`) es engañoso — no dice "no autorizado",
  dice "método no reconocido", pero es exactamente ese método el que usan
  todas las implementaciones de referencia (samsungctl, Home Assistant).
- **Editar `hud.html` no necesita reiniciar el server** (se lee de disco en
  cada `GET /hud`); editar `server_dlna.py` sí, y reiniciar corta la
  reproducción en curso — por eso cada fix de backend en esta sesión se
  coordinó con "¿reinicio ahora o esperamos a que termine la película?".

## 9. Pendientes reales

- Revisar en el TV el menú de Administrador de conexión de dispositivos
  para ver si el apagado remoto se puede desbloquear.
- Fase 3 (selector de TVs) quedó implementada pero sin QA exhaustiva con
  más de una TV real distinta en la red (solo hay una TV confirmada
  funcionando end-to-end; el escaneo de subred para encontrar otras no se
  probó con una segunda TV real presente).
