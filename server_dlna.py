#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_dlna.py — Servidor DLNA/UPnP minimalista para enviar un video local a un
televisor Samsung (probado en la serie NU7100) por la red WiFi, SIN AirPlay.

Además expone un HUD en vivo (estilo Iron Man) para monitorear la transferencia:
  - http://<ip-del-mac>:8200/hud        → panel visual
  - http://<ip-del-mac>:8200/stats.json → métricas crudas (JSON)

Cómo funciona el cast:
  - Levanta un "MediaServer" DLNA en tu Mac.
  - El TV lo descubre por SSDP y lo muestra en Source / Fuente.
  - El TV pide el archivo por HTTP (byte-range) y lo reproduce con SU reproductor
    nativo. No transcodifica: tu MP4 ya es H.264 + AAC.

Uso:
    python3 server_dlna.py "/ruta/al/video.mp4"

Variables de entorno (para pruebas, opcionales):
    DLNA_PORT=8201      → cambia el puerto HTTP
    DLNA_NO_SSDP=1      → no anuncia por SSDP (para probar sin ensuciar la LAN)

Requisitos: solo Python 3 (stdlib). ffprobe y ping/arp son opcionales (mejoran el HUD).
Para detener: Ctrl+C.
"""

import os
import re
import sys
import json
import atexit
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid
import urllib.request
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

# ----------------------------- Configuración -------------------------------
HTTP_PORT = int(os.environ.get("DLNA_PORT", "8200"))
NO_SSDP = os.environ.get("DLNA_NO_SSDP", "") == "1"
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
CHUNK = 256 * 1024  # 256 KB por bloque al transmitir
HUD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud.html")
TV_IP_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dlna_tv_ip")

VIDEO_EXTS = (".mp4", ".m4v", ".mkv", ".mov", ".avi", ".ts")
MIME_MAP = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".avi": "video/avi", ".ts": "video/mpeg",
}

# Biblioteca de videos escaneada (se llena en main si se pasa una carpeta).
LIBRARY = []  # [{id, title, path, size, size_h}]

# Estado global (se rellena en main)
STATE = {
    "ip": "127.0.0.1",
    "path": None,             # archivo que realmente se sirve (recortado si hay --start)
    "size": 0,
    "title": "video",
    "mime": "video/mp4",
    "duration_s": None,       # duración del archivo servido (fragmento si hay --start)
    "total_duration_s": None, # duración total de la peli original
    "start_offset_s": 0,      # dónde arranca el fragmento dentro de la peli
    "seek_temp": None,        # ruta del archivo temporal recortado (para borrarlo al salir)
    "lib_root": None,         # carpeta raíz de la biblioteca actual
    # UDN fijo (derivado de un namespace estable): reinicios reusan la misma
    # identidad y el TV no muestra iconos fantasma duplicados.
    "udn": "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "mac-dlna-cast.local")),
}

# ----------------------------- Métricas (HUD) ------------------------------
# Todo lo que el HUD necesita, protegido por un lock porque el server es multi-hilo.
_metrics_lock = threading.Lock()
METRICS = {
    "start_time": time.time(),
    "total_bytes": 0,
    "total_requests": 0,
    "contig_end": 0,              # bytes contiguos servidos desde el inicio (posición ≈ real)
    "peak_mbps": 0.0,
    "samples": deque(maxlen=4096),  # (timestamp, nbytes) para calcular throughput
    "clients": defaultdict(lambda: {
        "first_seen": None, "last_seen": None, "requests": 0,
        "bytes": 0, "user_agent": "",
    }),
    "tv_ip": None,                 # IP del último cliente que pidió media
    "tv_mac": None,
    "tv_ua": "",
    "tv_ping_ms": None,
    "tv_alive": False,
    "tv_last_ping": 0.0,
}


def record_request(ip, user_agent, start_offset):
    now = time.time()
    with _metrics_lock:
        METRICS["total_requests"] += 1
        c = METRICS["clients"][ip]
        if c["first_seen"] is None:
            c["first_seen"] = now
        c["last_seen"] = now
        c["requests"] += 1
        if user_agent:
            c["user_agent"] = user_agent
            METRICS["tv_ua"] = user_agent
        METRICS["tv_ip"] = ip


def record_bytes(ip, nbytes, current_offset, req_start):
    now = time.time()
    with _metrics_lock:
        METRICS["total_bytes"] += nbytes
        METRICS["samples"].append((now, nbytes))
        c = METRICS["clients"][ip]
        c["bytes"] += nbytes
        c["last_seen"] = now
        # Posición ≈ real: avanzamos el "borde contiguo" servido desde el inicio.
        # Un request que empieza pegado (o antes) del borde lo extiende; las
        # lecturas sueltas del final (el índice 'moov' del MP4) quedan como isla
        # y NO disparan la posición al 100%. Eso arregla el bug de la barra.
        if req_start <= METRICS["contig_end"] + 1 and current_offset > METRICS["contig_end"]:
            METRICS["contig_end"] = current_offset


def _mbps_over(window_s, now):
    """Megabits/s transferidos en los últimos `window_s` segundos."""
    cutoff = now - window_s
    total = sum(n for (t, n) in METRICS["samples"] if t >= cutoff)
    return (total * 8.0) / (window_s * 1_000_000.0)


def build_stats():
    now = time.time()
    with _metrics_lock:
        size = STATE["size"] or 1
        mbps_now = _mbps_over(2.0, now)
        if mbps_now > METRICS["peak_mbps"]:
            METRICS["peak_mbps"] = mbps_now

        # Historial: Mbps por segundo, últimos 60 s (para el gráfico).
        buckets = defaultdict(float)
        for (t, n) in METRICS["samples"]:
            sec = int(now - t)
            if 0 <= sec < 60:
                buckets[sec] += n
        history = [round((buckets.get(s, 0.0) * 8.0) / 1_000_000.0, 3)
                   for s in range(59, -1, -1)]  # index 0 = hace 59s, -1 = ahora

        playhead = METRICS["contig_end"]
        frag_ratio = min(1.0, playhead / size)       # avance dentro del fragmento servido
        total_dur = STATE["total_duration_s"]
        frag_dur = STATE["duration_s"]
        offset = STATE["start_offset_s"]
        pos_time = None
        ratio = frag_ratio
        if frag_dur:
            pos_time = offset + frag_ratio * frag_dur
            if total_dur:
                ratio = min(1.0, pos_time / total_dur)

        tv_ip = METRICS["tv_ip"]
        tv_last_seen = None
        if tv_ip and METRICS["clients"][tv_ip]["last_seen"]:
            tv_last_seen = now - METRICS["clients"][tv_ip]["last_seen"]

        stats = {
            "server": {
                "uptime_s": round(now - METRICS["start_time"], 1),
                "file_name": STATE["title"],
                "file_size": STATE["size"],
                "file_size_h": human_bytes(STATE["size"]),
                "ip": STATE["ip"],
                "port": HTTP_PORT,
                "duration_s": STATE["total_duration_s"],
                "duration_h": human_time(STATE["total_duration_s"]) if STATE["total_duration_s"] else None,
                "start_offset_s": STATE["start_offset_s"],
                "start_offset_h": human_time(STATE["start_offset_s"]) if STATE["start_offset_s"] else None,
            },
            "transfer": {
                "total_bytes": METRICS["total_bytes"],
                "total_bytes_h": human_bytes(METRICS["total_bytes"]),
                "total_requests": METRICS["total_requests"],
                "mbps_now": round(mbps_now, 2),
                "mbps_peak": round(METRICS["peak_mbps"], 2),
                "history": history,
            },
            "playback": {
                "playhead_bytes": playhead,
                "position_ratio": round(ratio, 4),
                "position_time_s": round(pos_time, 1) if pos_time is not None else None,
                "position_time_h": human_time(pos_time) if pos_time is not None else None,
            },
            "tv": {
                "connected": tv_last_seen is not None and tv_last_seen < 20,
                "ip": tv_ip,
                "mac": METRICS["tv_mac"],
                "user_agent": METRICS["tv_ua"],
                "ping_ms": METRICS["tv_ping_ms"],
                "alive": METRICS["tv_alive"],
                "last_seen_s": round(tv_last_seen, 1) if tv_last_seen is not None else None,
            },
        }
    return stats


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.2f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024
    return "%.2f TB" % n


def human_time(seconds):
    if seconds is None:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


# ----------------------- Salud del TV (ping / ARP) -------------------------
def probe_duration(path):
    """Duración del archivo vía ffprobe si está disponible; si no, None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        val = out.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def parse_timecode(s):
    """'40:00' -> 2400.0, '1:05:30' -> 3930.0, '90' -> 90.0, None si inválido."""
    s = (s or "").strip()
    try:
        if ":" in s:
            sec = 0.0
            for part in s.split(":"):
                sec = sec * 60 + float(part)
            return sec
        return float(s)
    except ValueError:
        return None


def build_seek_clip(src, offset_s):
    """Recorta la peli desde offset_s SIN recodificar (-c copy) y con el índice
    al inicio (+faststart). Devuelve la ruta del temporal, o None si falla."""
    d = os.path.dirname(src)
    # Barrer recortes huérfanos de corridas anteriores (si un SIGTERM/kill no los borró).
    for f in os.listdir(d):
        if f.startswith(".dlna_seek_") and f.endswith(".mp4"):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
    out = os.path.join(d, ".dlna_seek_%d.mp4" % os.getpid())
    print("  Preparando recorte desde %s (ffmpeg -c copy)... espera unos segundos"
          % human_time(offset_s), flush=True)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(offset_s), "-i", src,
             "-c", "copy", "-movflags", "+faststart",
             "-avoid_negative_ts", "make_zero", out],
            capture_output=True, text=True, timeout=900)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            print("  Recorte listo (%s)." % human_bytes(os.path.getsize(out)), flush=True)
            return out
        print("  ffmpeg falló: %s" % ((r.stderr or "")[-300:]), flush=True)
    except Exception as e:
        print("  Error ejecutando ffmpeg: %s" % e, flush=True)
    return None


def arp_lookup(ip):
    """MAC del TV desde la tabla ARP del sistema (best-effort)."""
    try:
        out = subprocess.run(["arp", "-n", ip], capture_output=True,
                             text=True, timeout=3)
        for tok in out.stdout.replace("\n", " ").split(" "):
            if tok.count(":") == 5:
                # macOS omite el cero a la izquierda (…37:e); lo normalizamos a …37:0e
                return ":".join(o.zfill(2) for o in tok.lower().split(":"))
    except Exception:
        pass
    return None


def ping_once(ip):
    """Latencia en ms al TV (macOS: ping -c1 -t1); None si no responde."""
    try:
        out = subprocess.run(["ping", "-c", "1", "-t", "1", ip],
                             capture_output=True, text=True, timeout=3)
        if "time=" in out.stdout:
            frag = out.stdout.split("time=", 1)[1]
            return float(frag.split(" ", 1)[0])
    except Exception:
        pass
    return None


def tv_health_thread(stop_event):
    """Cada 3 s: pinguea al TV conectado y resuelve su MAC. Alimenta el HUD."""
    while not stop_event.is_set():
        with _metrics_lock:
            ip = METRICS["tv_ip"]
            have_mac = METRICS["tv_mac"]
        if ip:
            ms = ping_once(ip)
            mac = have_mac or arp_lookup(ip)
            with _metrics_lock:
                METRICS["tv_ping_ms"] = round(ms, 1) if ms is not None else None
                METRICS["tv_alive"] = ms is not None
                METRICS["tv_last_ping"] = time.time()
                if mac:
                    METRICS["tv_mac"] = mac
        stop_event.wait(3)


# DLNA.ORG flags para streaming con seek por rango (operación 01 = range ok)
DLNA_PN = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def get_local_ip():
    """Descubre la IP local hacia la red (sin enviar nada realmente)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def base_url():
    return "http://%s:%d" % (STATE["ip"], HTTP_PORT)


def media_url():
    return base_url() + "/media/0"


# --------------------------- Documentos XML --------------------------------
def device_description():
    return """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>Mac DLNA Cast</friendlyName>
    <manufacturer>DIY</manufacturer>
    <manufacturerURL>http://localhost</manufacturerURL>
    <modelDescription>Servidor DLNA minimalista</modelDescription>
    <modelName>MacCast</modelName>
    <modelNumber>1.0</modelNumber>
    <serialNumber>0001</serialNumber>
    <UDN>%s</UDN>
    <dlna:X_DLNADOC xmlns:dlna="urn:schemas-dlna-org:device-1-0">DMS-1.50</dlna:X_DLNADOC>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/cd.xml</SCPDURL>
        <controlURL>/control/cd</controlURL>
        <eventSubURL>/event/cd</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/cm.xml</SCPDURL>
        <controlURL>/control/cm</controlURL>
        <eventSubURL>/event/cm</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>""" % STATE["udn"]


CONTENT_DIRECTORY_SCPD = """<?xml version="1.0" encoding="UTF-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSystemUpdateID</name>
      <argumentList>
        <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSortCapabilities</name>
      <argumentList>
        <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSearchCapabilities</name>
      <argumentList>
        <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType>
      <allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList>
    </stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""


CONNECTION_MANAGER_SCPD = """<?xml version="1.0" encoding="UTF-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>GetProtocolInfo</name>
      <argumentList>
        <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
        <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""


def didl_item():
    """DIDL-Lite con nuestro único video como hijo de la raíz (ObjectID 0)."""
    res_proto = "http-get:*:%s:%s" % (STATE["mime"], DLNA_PN)
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
        '<item id="1" parentID="0" restricted="1">'
        '<dc:title>%s</dc:title>'
        '<upnp:class>object.item.videoItem</upnp:class>'
        '<res protocolInfo="%s" size="%d">%s</res>'
        '</item>'
        '</DIDL-Lite>'
    ) % (escape(STATE["title"]), res_proto, STATE["size"], escape(media_url()))


def didl_root_metadata():
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<container id="0" parentID="-1" restricted="1" childCount="1">'
        '<dc:title>Mac DLNA Cast</dc:title>'
        '<upnp:class>object.container.storageFolder</upnp:class>'
        '</container>'
        '</DIDL-Lite>'
    )


def soap_browse_response(browse_flag):
    if browse_flag == "BrowseMetadata":
        didl = didl_root_metadata()
        number, total = 1, 1
    else:  # BrowseDirectChildren
        didl = didl_item()
        number, total = 1, 1
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        '<Result>%s</Result>'
        '<NumberReturned>%d</NumberReturned>'
        '<TotalMatches>%d</TotalMatches>'
        '<UpdateID>1</UpdateID>'
        '</u:BrowseResponse>'
        '</s:Body></s:Envelope>'
    ) % (escape(didl), number, total)
    return body


def soap_protocolinfo_response():
    source = "http-get:*:%s:%s" % (STATE["mime"], DLNA_PN)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        '<u:GetProtocolInfoResponse xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
        '<Source>%s</Source><Sink></Sink>'
        '</u:GetProtocolInfoResponse>'
        '</s:Body></s:Envelope>'
    ) % escape(source)


# ---------------------- Control del TV (AVTransport / DMC) -----------------
# El TV Samsung expone un MediaRenderer con AVTransport: podemos ORDENARLE
# reproducir, pausar, parar y hacer seek. Eso da tiempo absoluto real y control
# del tiempo en caliente desde el HUD, sin recortar el archivo.
AVT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
RC_SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"
DIAL_SERVICE = "urn:dial-multiscreen-org:service:dial:1"
TV_CTRL = {"control_url": None, "rc_url": None, "dial_url": None}


def _soap_call(url, service, action, inner_xml, timeout=6):
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>'
    ) % (action, service, inner_xml, action)
    req = urllib.request.Request(
        url, data=body.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": '"%s#%s"' % (service, action),
        }, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _read_tv_cache():
    try:
        with open(TV_IP_CACHE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write_tv_cache(ip):
    try:
        with open(TV_IP_CACHE, "w") as f:
            f.write(ip)
    except OSError:
        pass


def autodiscover_tv_ip(timeout=0.4):
    """Escanea la subred local buscando el MediaRenderer del TV (puerto 9197),
    en paralelo. Así no hace falta pasar la IP a mano. Devuelve la IP o None."""
    parts = STATE["ip"].split(".")
    if len(parts) != 4:
        return None
    base = ".".join(parts[:3])
    found = []
    lock = threading.Lock()

    def probe(ip):
        try:
            data = urllib.request.urlopen("http://%s:9197/dmr" % ip,
                                          timeout=timeout).read(800).decode("utf-8", "ignore")
            if "MediaRenderer" in data:
                with lock:
                    found.append(ip)
        except Exception:
            pass

    threads = [threading.Thread(target=probe, args=("%s.%d" % (base, i),), daemon=True)
               for i in range(1, 255)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 0.3)
    return found[0] if found else None


def discover_renderer(timeout=3):
    """Encuentra el controlURL de AVTransport del TV (MediaRenderer). Cachea."""
    if TV_CTRL["control_url"]:
        return TV_CTRL["control_url"]
    msearch = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\nMX: 2\r\n'
        "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    tv_ip = METRICS.get("tv_ip")
    loc = None
    try:
        for _ in range(2):
            sock.sendto(msearch, (SSDP_ADDR, SSDP_PORT))
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            if tv_ip and addr[0] != tv_ip:
                continue
            m = re.search(r"LOCATION:\s*(\S+)", data.decode("utf-8", "ignore"), re.I)
            if m:
                loc = m.group(1)
                break
    finally:
        sock.close()
    # 1) LOCATION por M-SEARCH multicast (poco fiable entre bandas 5/2.4 GHz).
    if loc and _process_desc(loc):
        return TV_CTRL["control_url"]
    # 2) Fallback UNICAST a IPs conocidas: tv_ip (se llena cuando el TV pide media),
    #    el hint DLNA_TV_IP, y la última IP cacheada de una corrida anterior.
    for ip in dict.fromkeys([x for x in (tv_ip, os.environ.get("DLNA_TV_IP"),
                                         _read_tv_cache()) if x]):
        if _process_desc("http://%s:9197/dmr" % ip):
            _write_tv_cache(ip)
            return TV_CTRL["control_url"]
    # 3) Auto-detección: escanea la subred y encuentra el TV solo (sin IP en duro).
    ip = autodiscover_tv_ip()
    if ip and _process_desc("http://%s:9197/dmr" % ip):
        _write_tv_cache(ip)
        return TV_CTRL["control_url"]
    return None


def _process_desc(loc):
    """Descarga el device desc en 'loc' y cachea los controlURL. -> avt o None."""
    try:
        xml = urllib.request.urlopen(loc, timeout=4).read().decode("utf-8", "ignore")
    except Exception:
        return None
    bm = re.match(r"(https?://[^/]+)", loc)
    if not bm:
        return None
    base = bm.group(1)
    avt = _extract_control_url(xml, "AVTransport", base)
    if not avt:
        return None
    TV_CTRL["control_url"] = avt
    TV_CTRL["rc_url"] = _extract_control_url(xml, "RenderingControl", base)
    return avt


def _extract_control_url(xml, service_name, base):
    """controlURL absoluto del <service> cuyo tipo contiene service_name."""
    m = re.search(service_name + r":1.*?<controlURL>\s*([^<]+?)\s*</controlURL>",
                  xml, re.S)
    if not m:
        return None
    ctrl = m.group(1).strip()
    if not ctrl.startswith("http"):
        ctrl = base + (ctrl if ctrl.startswith("/") else "/" + ctrl)
    return ctrl


def _hms(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def _tc_to_secs(tc):
    if not tc or tc == "NOT_IMPLEMENTED":
        return None
    try:
        s = 0.0
        for p in tc.split(":"):
            s = s * 60 + float(p)
        return s
    except ValueError:
        return None


def tv_set_uri():
    inner = ("<InstanceID>0</InstanceID><CurrentURI>%s</CurrentURI>"
             "<CurrentURIMetaData>%s</CurrentURIMetaData>"
             ) % (escape(media_url()), escape(didl_item()))
    return _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "SetAVTransportURI", inner)


def tv_play():
    return _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "Play",
                      "<InstanceID>0</InstanceID><Speed>1</Speed>")


def tv_pause():
    return _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "Pause",
                      "<InstanceID>0</InstanceID>")


def tv_stop():
    return _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "Stop",
                      "<InstanceID>0</InstanceID>")


def tv_seek(seconds):
    inner = ("<InstanceID>0</InstanceID><Unit>REL_TIME</Unit><Target>%s</Target>"
             ) % _hms(seconds)
    return _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "Seek", inner)


def tv_position():
    """(reltime_s, duration_s, state) reales reportados por el TV."""
    try:
        xml = _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "GetPositionInfo",
                         "<InstanceID>0</InstanceID>")
        rel = re.search(r"<RelTime>([^<]*)</RelTime>", xml)
        dur = re.search(r"<TrackDuration>([^<]*)</TrackDuration>", xml)
        xml2 = _soap_call(TV_CTRL["control_url"], AVT_SERVICE, "GetTransportInfo",
                          "<InstanceID>0</InstanceID>")
        st = re.search(r"<CurrentTransportState>([^<]*)</CurrentTransportState>", xml2)
        return (_tc_to_secs(rel.group(1)) if rel else None,
                _tc_to_secs(dur.group(1)) if dur else None,
                st.group(1) if st else None)
    except Exception:
        return None, None, None


def cast_to_tv(seek_s=0):
    """Ordena al TV reproducir nuestro media y saltar a seek_s. -> (ok, mensaje)."""
    if not discover_renderer():
        return False, "No encontré el MediaRenderer del TV"
    try:
        tv_set_uri()
        time.sleep(0.4)
        tv_play()
        if seek_s and seek_s > 0:
            # El Samsung rechaza Seek mientras carga (TRANSITIONING): esperar a PLAYING.
            for _ in range(12):
                _, _, st = tv_position()
                if st == "PLAYING":
                    break
                time.sleep(0.6)
            tv_seek(seek_s)
        return True, "cast enviado"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def tv_get_volume():
    if not TV_CTRL.get("rc_url"):
        return None
    try:
        xml = _soap_call(TV_CTRL["rc_url"], RC_SERVICE, "GetVolume",
                         "<InstanceID>0</InstanceID><Channel>Master</Channel>")
        m = re.search(r"<CurrentVolume>([^<]+)</CurrentVolume>", xml)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def tv_set_volume(level):
    if not TV_CTRL.get("rc_url"):
        discover_renderer()
    level = max(0, min(100, int(level)))
    _soap_call(TV_CTRL["rc_url"], RC_SERVICE, "SetVolume",
               "<InstanceID>0</InstanceID><Channel>Master</Channel>"
               "<DesiredVolume>%d</DesiredVolume>" % level)
    return level


def tv_set_mute(on):
    if not TV_CTRL.get("rc_url"):
        discover_renderer()
    _soap_call(TV_CTRL["rc_url"], RC_SERVICE, "SetMute",
               "<InstanceID>0</InstanceID><Channel>Master</Channel>"
               "<DesiredMute>%d</DesiredMute>" % (1 if on else 0))
    return bool(on)


def discover_dial():
    """controlURL del servicio DIAL del TV (para SendKeyCode = teclas del control)."""
    if TV_CTRL.get("dial_url"):
        return TV_CTRL["dial_url"]
    ip = METRICS.get("tv_ip") or os.environ.get("DLNA_TV_IP")
    if not ip:
        return None
    try:
        xml = urllib.request.urlopen("http://%s:7678/nservice/" % ip,
                                     timeout=4).read().decode("utf-8", "ignore")
    except Exception:
        return None
    TV_CTRL["dial_url"] = _extract_control_url(xml, "dial", "http://%s:7678" % ip)
    return TV_CTRL["dial_url"]


def tv_send_key(key):
    """Envía una tecla del control remoto al TV (KEY_VOLUP/KEY_VOLDOWN/KEY_MUTE…).
    El TV la reenvía por CEC a la barra de sonido ARC. Devuelve la respuesta."""
    url = discover_dial()
    if not url:
        raise RuntimeError("no encontré el servicio DIAL del TV")
    inner = "<KeyCode>%s</KeyCode><KeyDescription></KeyDescription>" % key
    return _soap_call(url, DIAL_SERVICE, "SendKeyCode", inner)


# ------------------------- Biblioteca de videos ----------------------------
def scan_library(root):
    """Escanea recursivamente 'root' por videos y devuelve la lista ordenada."""
    items = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.startswith(".") or not f.lower().endswith(VIDEO_EXTS):
                continue
            full = os.path.join(dirpath, f)
            try:
                sz = os.path.getsize(full)
            except OSError:
                continue
            items.append({"id": len(items), "title": os.path.splitext(f)[0],
                          "path": full, "size": sz, "size_h": human_bytes(sz)})
    return items


def list_dirs(path):
    """Lista subcarpetas de 'path' (con conteo de videos directos) para el
    navegador de carpetas del HUD."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        path = os.path.expanduser("~")

    def count_videos(d):
        try:
            return sum(1 for f in os.listdir(d)
                       if f.lower().endswith(VIDEO_EXTS) and not f.startswith("."))
        except OSError:
            return 0

    dirs = []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            full = os.path.join(path, name)
            if name.startswith(".") or not os.path.isdir(full):
                continue
            dirs.append({"name": name, "path": full, "videos": count_videos(full)})
    except OSError:
        pass
    return {"path": path, "parent": os.path.dirname(path),
            "dirs": dirs, "videos_here": count_videos(path)}


def set_library(path):
    """Reemplaza la biblioteca escaneando una carpeta nueva. -> (ok, count|msg)."""
    global LIBRARY
    path = os.path.abspath(os.path.expanduser(path or ""))
    if not os.path.isdir(path):
        return False, "no es una carpeta válida"
    LIBRARY = scan_library(path)
    STATE["lib_root"] = path
    return True, len(LIBRARY)


def load_media(idx, and_cast=True):
    """Cambia el video servido al de la biblioteca (idx) y lo castea al TV."""
    if idx < 0 or idx >= len(LIBRARY):
        return False, "índice inválido"
    it = LIBRARY[idx]
    path = it["path"]
    mime = MIME_MAP.get(os.path.splitext(path)[1].lower(), "video/mp4")
    with _metrics_lock:
        STATE["path"] = path
        STATE["size"] = it["size"]
        STATE["title"] = it["title"]
        STATE["mime"] = mime
        STATE["start_offset_s"] = 0
        STATE["duration_s"] = None
        STATE["total_duration_s"] = None
        METRICS["contig_end"] = 0

    def _dur():
        d = probe_duration(path)
        STATE["total_duration_s"] = d
        STATE["duration_s"] = d
    threading.Thread(target=_dur, daemon=True).start()

    if and_cast:
        # El cast va en un hilo: SetURI+Play tarda varios segundos y no debe
        # bloquear la respuesta de /api/load.
        threading.Thread(target=cast_to_tv, args=(0,), daemon=True).start()
    return True, it["title"]


# ----------------------------- HTTP handler --------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle_one_request(self):
        # El Samsung abre conexiones de sondeo y las resetea antes de pedir nada.
        # Eso es normal; evitamos el traceback ruidoso de socketserver.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, fmt, *args):
        # Silenciamos el ruido del polling del HUD (cada segundo).
        if any(x in (self.path or "") for x in ("/stats.json", "/api/tvpos")):
            return
        print("  [HTTP] %s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_bytes(self, data, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_xml(self, xml):
        self._send_bytes(xml.encode("utf-8"), 'text/xml; charset="utf-8"')

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/desc.xml":
            self._send_xml(device_description())
        elif p == "/cd.xml":
            self._send_xml(CONTENT_DIRECTORY_SCPD)
        elif p == "/cm.xml":
            self._send_xml(CONNECTION_MANAGER_SCPD)
        elif p == "/stats.json":
            self._send_bytes(json.dumps(build_stats()).encode("utf-8"),
                             "application/json")
        elif p in ("/hud", "/", "/index.html"):
            self._serve_hud()
        elif p.startswith("/api/"):
            self._handle_api(p)
        elif p.startswith("/media/"):
            self._serve_media(head_only=False)
        else:
            self.send_error(404)

    def _api_json(self, obj):
        self._send_bytes(json.dumps(obj).encode("utf-8"), "application/json")

    def _handle_api(self, p):
        q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        try:
            if p == "/api/play":
                tv_play(); self._api_json({"ok": True})
            elif p == "/api/pause":
                tv_pause(); self._api_json({"ok": True})
            elif p == "/api/stop":
                tv_stop(); self._api_json({"ok": True})
            elif p == "/api/seek":
                t = parse_timecode(q.get("t", ["0"])[0]) or 0
                tv_seek(t); self._api_json({"ok": True, "seek_s": t})
            elif p == "/api/cast":
                t = parse_timecode(q.get("seek", ["0"])[0]) or 0
                ok, msg = cast_to_tv(t); self._api_json({"ok": ok, "msg": msg})
            elif p == "/api/tvpos":
                rel, dur, st = tv_position()
                self._api_json({
                    "reltime_s": rel, "duration_s": dur,
                    "reltime_h": human_time(rel) if rel is not None else None,
                    "duration_h": human_time(dur) if dur is not None else None,
                    "state": st})
            elif p == "/api/library":
                self._api_json({
                    "items": [{"id": x["id"], "title": x["title"], "size_h": x["size_h"]}
                              for x in LIBRARY],
                    "current": STATE["title"],
                    "root": STATE.get("lib_root")})
            elif p == "/api/load":
                ok, msg = load_media(int(q.get("id", ["-1"])[0]))
                self._api_json({"ok": ok, "msg": msg})
            elif p == "/api/volume":
                if "delta" in q:
                    lvl = (tv_get_volume() or 0) + int(q["delta"][0])
                else:
                    lvl = int(q.get("level", ["10"])[0])
                lvl = tv_set_volume(lvl)
                self._api_json({"ok": True, "volume": lvl})
            elif p == "/api/mute":
                on = tv_set_mute(q.get("on", ["1"])[0] == "1")
                self._api_json({"ok": True, "mute": on})
            elif p == "/api/tvvol":
                self._api_json({"volume": tv_get_volume()})
            elif p == "/api/key":
                code = q.get("code", [""])[0]
                tv_send_key(code)
                self._api_json({"ok": True, "key": code})
            elif p == "/api/dirs":
                self._api_json(list_dirs(q.get("path", ["~"])[0]))
            elif p == "/api/setlibrary":
                ok, res = set_library(q.get("path", [""])[0])
                self._api_json({"ok": ok, "count": res} if ok
                               else {"ok": False, "error": res})
            else:
                self.send_error(404)
        except Exception as e:
            self._api_json({"ok": False, "error": str(e)})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/media/"):
            self._serve_media(head_only=True)
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if p == "/control/cd":
            flag = "BrowseDirectChildren"
            if b"BrowseMetadata" in body:
                flag = "BrowseMetadata"
            self._send_xml(soap_browse_response(flag))
        elif p == "/control/cm":
            self._send_xml(soap_protocolinfo_response())
        else:
            self.send_error(404)

    def do_SUBSCRIBE(self):
        # Algunos TVs se suscriben a eventos; respondemos OK para no trabar.
        self.send_response(200)
        self.send_header("SID", "uuid:" + str(uuid.uuid4()))
        self.send_header("TIMEOUT", "Second-1800")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_hud(self):
        try:
            with open(HUD_FILE, "rb") as f:
                data = f.read()
            self._send_bytes(data, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send_bytes(b"<h1>hud.html no encontrado</h1>",
                             "text/html; charset=utf-8", code=404)

    def _serve_media(self, head_only=False):
        # Snapshot atómico del estado: load_media puede cambiar path/size/mime en
        # caliente (desde /api/load). Sin esto, un request en vuelo mezclaría el
        # size viejo con el path nuevo y serviría bytes inconsistentes.
        with _metrics_lock:
            size = STATE["size"]
            path = STATE["path"]
            mime = STATE["mime"]

        start, end = 0, size - 1
        is_range = False
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            is_range = True
            spec = rng[len("bytes="):].split(",")[0].strip()
            s, _, e = spec.partition("-")
            try:
                if s.strip() == "":
                    start = max(0, size - int(e))   # sufijo: últimos N bytes
                    end = size - 1
                else:
                    start = int(s)
                    end = int(e) if e.strip() else size - 1
            except ValueError:
                self.send_error(400, "Bad Range")
                return
            start = max(0, start)
            end = min(end, size - 1)

        length = end - start + 1

        # Rango fuera de límite: 416 (no un Content-Length negativo).
        if is_range and (start >= size or length <= 0):
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.close_connection = True
            self.end_headers()
            return

        client_ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "")
        record_request(client_ip, ua, start)

        if is_range:
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)

        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        # Cabeceras DLNA que los Samsung suelen exigir
        self.send_header("contentFeatures.dlna.org", DLNA_PN)
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("Connection", "close")
        self.close_connection = True  # honrar de verdad el Connection: close
        self.end_headers()

        if head_only:
            return

        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    buf = f.read(min(CHUNK, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
                    record_bytes(client_ip, len(buf), start + (length - remaining), start)
        except (BrokenPipeError, ConnectionResetError):
            # El TV cortó la conexión (seek, stop): normal.
            pass
        except OSError:
            # El archivo desapareció (borrado/temporal barrido): cerrar limpio.
            self.close_connection = True


# ------------------------------ SSDP -------------------------------------
def ssdp_targets():
    udn = STATE["udn"]
    return [
        ("upnp:rootdevice", udn + "::upnp:rootdevice"),
        (udn, udn),
        ("urn:schemas-upnp-org:device:MediaServer:1",
         udn + "::urn:schemas-upnp-org:device:MediaServer:1"),
        ("urn:schemas-upnp-org:service:ContentDirectory:1",
         udn + "::urn:schemas-upnp-org:service:ContentDirectory:1"),
        ("urn:schemas-upnp-org:service:ConnectionManager:1",
         udn + "::urn:schemas-upnp-org:service:ConnectionManager:1"),
    ]


def ssdp_response(st, usn):
    return (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        "EXT:\r\n"
        "LOCATION: %s/desc.xml\r\n"
        "SERVER: Darwin/UPnP/1.0 MacCast/1.0\r\n"
        "ST: %s\r\n"
        "USN: %s\r\n"
        "\r\n"
    ) % (base_url(), st, usn)


def ssdp_notify(nt, usn):
    return (
        "NOTIFY * HTTP/1.1\r\n"
        "HOST: %s:%d\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        "LOCATION: %s/desc.xml\r\n"
        "NT: %s\r\n"
        "NTS: ssdp:alive\r\n"
        "SERVER: Darwin/UPnP/1.0 MacCast/1.0\r\n"
        "USN: %s\r\n"
        "\r\n"
    ) % (SSDP_ADDR, SSDP_PORT, base_url(), nt, usn)


def ssdp_server(stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.bind(("", SSDP_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR),
                       socket.inet_aton(STATE["ip"]))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)

    print("  [SSDP] Escuchando descubrimiento en %s:%d" % (SSDP_ADDR, SSDP_PORT),
          flush=True)
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        text = data.decode("utf-8", "ignore")
        if "M-SEARCH" not in text or "ssdp:discover" not in text:
            continue
        st_req = None
        for line in text.split("\r\n"):
            if line.upper().startswith("ST:"):
                st_req = line.split(":", 1)[1].strip()
                break
        for st, usn in ssdp_targets():
            if st_req in ("ssdp:all", None) or st_req == st:
                try:
                    sock.sendto(ssdp_response(st, usn).encode("utf-8"), addr)
                except OSError:
                    pass
    sock.close()


def ssdp_announcer(stop_event):
    """Envía NOTIFY ssdp:alive periódicamente para que el TV nos encuentre."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(STATE["ip"]))
    while not stop_event.is_set():
        for nt, usn in ssdp_targets():
            try:
                sock.sendto(ssdp_notify(nt, usn).encode("utf-8"),
                            (SSDP_ADDR, SSDP_PORT))
            except OSError:
                pass
        stop_event.wait(30)
    sock.close()


# ------------------------------- main ------------------------------------
_cleaned = threading.Event()


def cleanup_temp():
    """Borra el recorte temporal. Idempotente y seguro para atexit/señales."""
    if _cleaned.is_set():
        return
    _cleaned.set()
    temp = STATE.get("seek_temp")
    if temp and os.path.isfile(temp):
        try:
            os.remove(temp)
            print("Temporal de recorte borrado.", flush=True)
        except OSError:
            pass


def main():
    global LIBRARY
    usage = ('Uso: python3 server_dlna.py <archivo.mp4 | carpeta> [--start MM:SS] [--library DIR]\n'
             '  <archivo>   sirve ese video    |    <carpeta>   escanea y sirve el primero\n'
             '  --start MM:SS   arranca el video en esa posición (recorte ffmpeg)\n'
             '  --library DIR   raíz de la biblioteca navegable/reproducible desde el HUD')
    args = sys.argv[1:]

    def take_opt(name):
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                print(usage); sys.exit(1)
            val = args[i + 1]; del args[i:i + 2]
            return val
        return None

    start_spec = take_opt("--start")
    lib_root = take_opt("--library")

    if not args:
        print(usage); sys.exit(1)
    src = os.path.abspath(args[0])

    # El argumento puede ser un archivo (sirve ese) o una carpeta (biblioteca).
    if os.path.isdir(src):
        lib_root = lib_root or src
        LIBRARY = scan_library(lib_root)
        if not LIBRARY:
            print("No encontré videos en: %s" % lib_root); sys.exit(1)
        src = LIBRARY[0]["path"]
    elif os.path.isfile(src):
        lib_root = lib_root or os.path.dirname(src)
        LIBRARY = scan_library(lib_root)
    else:
        print("No existe: %s" % src); sys.exit(1)
    STATE["lib_root"] = lib_root

    serve_path = src
    if start_spec is not None:
        offset_s = parse_timecode(start_spec)
        if offset_s is None or offset_s < 0:
            print("Valor de --start inválido. Usa MM:SS, HH:MM:SS o segundos.")
            sys.exit(1)
        serve_path = build_seek_clip(src, offset_s)
        if serve_path is None:
            print("No se pudo preparar el recorte. Arranca sin --start o revisa ffmpeg.")
            sys.exit(1)
        STATE["seek_temp"] = serve_path
        STATE["start_offset_s"] = offset_s
        # Limpieza garantizada del temporal ante salida normal o SIGTERM (kill/pkill).
        atexit.register(cleanup_temp)
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

    STATE["ip"] = get_local_ip()
    STATE["path"] = serve_path
    STATE["size"] = os.path.getsize(serve_path)
    STATE["title"] = os.path.splitext(os.path.basename(src))[0]
    STATE["mime"] = MIME_MAP.get(os.path.splitext(serve_path)[1].lower(), "video/mp4")
    METRICS["start_time"] = time.time()

    # Las duraciones (ffprobe) se calculan en segundo plano para NO retrasar el
    # arranque del stream: el server sirve al instante y el HUD las llena en ~1s.
    def _load_durations():
        STATE["total_duration_s"] = probe_duration(src)
        STATE["duration_s"] = (probe_duration(serve_path)
                               if serve_path != src else STATE["total_duration_s"])
    threading.Thread(target=_load_durations, daemon=True).start()

    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    stop_event = threading.Event()

    threads = [
        threading.Thread(target=httpd.serve_forever, daemon=True),
        threading.Thread(target=tv_health_thread, args=(stop_event,), daemon=True),
    ]
    if not NO_SSDP:
        threads.append(threading.Thread(target=ssdp_server, args=(stop_event,), daemon=True))
        threads.append(threading.Thread(target=ssdp_announcer, args=(stop_event,), daemon=True))
    for t in threads:
        t.start()

    dur = human_time(STATE["duration_s"]) or "desconocida"
    print("=" * 62)
    print("  Servidor DLNA activo" + ("  (SSDP OFF - modo prueba)" if NO_SSDP else ""))
    print("  Archivo : %s" % STATE["title"])
    print("  Tamaño  : %s   Duración: %s" % (human_bytes(STATE["size"]), dur))
    if STATE["start_offset_s"]:
        print("  Inicio  : %s  (arranca en esta posición, recorte con ffmpeg)"
              % human_time(STATE["start_offset_s"]))
    print("  Media   : %s" % media_url())
    print("  HUD     : %s/hud   <-- ábrelo en tu navegador" % base_url())
    print("-" * 62)
    print("  EN EL TV: pulsa Source / Fuente y busca 'Mac DLNA Cast'.")
    print("  Ctrl+C para detener.")
    print("=" * 62, flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo...")
        stop_event.set()
        httpd.shutdown()
        cleanup_temp()


if __name__ == "__main__":
    main()
