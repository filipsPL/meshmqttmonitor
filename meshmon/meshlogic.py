"""Pure Meshtastic helpers (no UI / MQTT): node IDs, traceroute and neighbour-info decoding"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

BROADCAST = "!ffffffff"
SNR_UNKNOWN = -128  # int8 sentinel used by traceroute SNR lists


def format_node_id(node_id: Any) -> str:
    """Convert any node ID format to the standard Meshtastic form: '!' + 8 hex digits"""
    if isinstance(node_id, bool):
        return str(node_id)
    if isinstance(node_id, int):
        return f"!{node_id & 0xFFFFFFFF:08x}"
    if isinstance(node_id, str):
        if node_id.startswith("!"):
            return node_id.lower()
        try:
            return f"!{int(node_id) & 0xFFFFFFFF:08x}"
        except ValueError:
            return node_id
    return str(node_id)


def _snr_db(raw: Any) -> Optional[float]:
    """Traceroute SNRs are int8 values in quarter-dB steps"""
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw == SNR_UNKNOWN:
        return None
    return raw / 4


def _path(origin: Any, route: Any, dest: Any, snrs: Any) -> Optional[Tuple[List[str], List[Optional[float]]]]:
    if not isinstance(route, list):
        return None
    nodes = [format_node_id(origin)] + [format_node_id(n) for n in route] + [format_node_id(dest)]
    if isinstance(snrs, list) and len(snrs) == len(nodes) - 1:
        return nodes, [_snr_db(v) for v in snrs]
    return nodes, [None] * (len(nodes) - 1)


def decode_traceroute(message: Dict[str, Any]) -> List[Tuple[str, List[str], List[Optional[float]]]]:
    """Return [(direction, nodes, snr_per_hop)] for a traceroute message.

    snr_per_hop[i] is the SNR (dB) at which nodes[i + 1] heard nodes[i].
    The JSON has no request/reply flag. A packet carrying route_back/snr_back is a reply
    (from = the traced node, to = the requester); otherwise it is read as a request in
    transit (from = requester, to = target).
    """
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return []
    src, dst = message.get("from"), message.get("to")
    if src is None or dst is None:
        return []
    reply = "route_back" in payload or "snr_back" in payload
    requester, target = (dst, src) if reply else (src, dst)

    results = []
    towards = _path(requester, payload.get("route"), target, payload.get("snr_towards"))
    if towards:
        results.append(("towards", *towards))
    if reply:
        back = _path(target, payload.get("route_back"), requester, payload.get("snr_back"))
        if back:
            results.append(("back", *back))
    return results


def decode_neighborinfo(message: Dict[str, Any]) -> Tuple[Optional[str], List[Tuple[str, Optional[float]]]]:
    """Return (reporting_node, [(neighbour, snr_db)]) - the neighbours the node can hear"""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None, []
    node = payload.get("node_id", message.get("from"))
    if node is None:
        return None, []
    neighbours = []
    for entry in payload.get("neighbors") or []:
        if isinstance(entry, dict) and entry.get("node_id") is not None:
            snr = entry.get("snr")
            neighbours.append((format_node_id(entry["node_id"]), snr if isinstance(snr, (int, float)) else None))
    return format_node_id(node), neighbours


def extract_links(message: Dict[str, Any]) -> List[Tuple[str, str, Optional[float], str]]:
    """Radio links implied by a message as (transmitter, receiver, snr_db, source)"""
    msg_type = str(message.get("type", "")).lower()
    links = []
    if msg_type == "neighborinfo":
        node, neighbours = decode_neighborinfo(message)
        for neighbour, snr in neighbours:
            links.append((neighbour, node, snr, "neighborinfo"))
    elif msg_type == "traceroute":
        for _direction, nodes, snrs in decode_traceroute(message):
            for i, snr in enumerate(snrs):
                links.append((nodes[i], nodes[i + 1], snr, "traceroute"))
    return links


def extract_position(message: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return {lat, lon[, alt]} in degrees from a position payload, or None"""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    try:
        lat = int(payload["latitude_i"]) * 1e-7
        lon = int(payload["longitude_i"]) * 1e-7
    except (KeyError, ValueError, TypeError):
        return None
    if (lat == 0 and lon == 0) or abs(lat) > 90 or abs(lon) > 180:
        return None
    pos = {"lat": lat, "lon": lon}
    if isinstance(payload.get("altitude"), (int, float)):
        pos["alt"] = float(payload["altitude"])
    return pos


def extract_telemetry(message: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return device metrics {battery, voltage, chan_util, air_util} present in a telemetry payload"""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    fields = {"battery_level": "battery", "voltage": "voltage", "channel_utilization": "chan_util", "air_util_tx": "air_util"}
    out = {}
    for src, dst in fields.items():
        value = payload.get(src)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[dst] = float(value)
    return out or None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def osm_link(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=15/{lat:.5f}/{lon:.5f}"


def build_gpx(waypoints: Iterable[Tuple[str, float, float, Optional[float]]]) -> str:
    """GPX 1.1 document from (name, lat, lon, alt) waypoints"""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<gpx version="1.1" creator="meshmqttmonitor" xmlns="http://www.topografix.com/GPX/1/1">']
    for name, lat, lon, alt in waypoints:
        ele = f"<ele>{alt:.0f}</ele>" if alt is not None else ""
        parts.append(f'<wpt lat="{lat:.7f}" lon="{lon:.7f}">{ele}<name>{xml_escape(name)}</name></wpt>')
    parts.append("</gpx>")
    return "\n".join(parts) + "\n"
