"""Decode Meshtastic MQTT protobuf (ServiceEnvelope) packets into the JSON-style dicts the monitor uses.

Topics `<root>/2/e/<channel>/<gateway>` carry ServiceEnvelopes whose packets are usually AES-CTR
encrypted with the channel key; `<root>/2/map/` carries unencrypted MapReports.
Packets that cannot be decrypted are emitted with type "encrypted" so the traffic stays visible.
"""

import base64
import warnings
from typing import Any, Dict, Iterable, List, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

from .meshlogic import format_node_id

DEFAULT_PSK = bytes([0xD4, 0xF1, 0xBB, 0x3A, 0x20, 0x29, 0x07, 0x59, 0xF0, 0xBC, 0xFF, 0xAB, 0xCF, 0x4E, 0x69, 0x01])
PortNum = portnums_pb2.PortNum

# Non-protobuf payloads (e.g. plain text on a binary topic) make the parser warn; the caller handles them
warnings.filterwarnings("ignore", category=RuntimeWarning, module="protodecode")


def expand_psk(psk_b64: str) -> Optional[bytes]:
    """Channel key as stored in the app / config (base64) -> AES key; a 1-byte key selects a default-key variant"""
    try:
        psk = base64.b64decode(psk_b64)
    except (ValueError, TypeError):
        return None
    if len(psk) == 1:
        if psk[0] == 0:
            return None  # unencrypted channel
        return DEFAULT_PSK[:-1] + bytes([(DEFAULT_PSK[-1] + psk[0] - 1) & 0xFF])
    if len(psk) in (16, 32):
        return psk
    return None


def decrypt(encrypted: bytes, key: bytes, packet_id: int, from_node: int) -> bytes:
    nonce = packet_id.to_bytes(8, "little") + from_node.to_bytes(4, "little") + bytes(4)
    decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
    return decryptor.update(encrypted) + decryptor.finalize()


def _try_data(raw: bytes) -> Optional[mesh_pb2.Data]:
    try:
        data = mesh_pb2.Data.FromString(raw)
        PortNum.Name(data.portnum)
    except (DecodeError, ValueError):
        return None
    return data if data.portnum != PortNum.UNKNOWN_APP else None


def _position(raw: bytes) -> Dict[str, Any]:
    pos = mesh_pb2.Position.FromString(raw)
    out: Dict[str, Any] = {"latitude_i": pos.latitude_i, "longitude_i": pos.longitude_i}
    if pos.altitude:
        out["altitude"] = pos.altitude
    if pos.time:
        out["time"] = pos.time
    return out


def _nodeinfo(raw: bytes, fallback_id: str) -> Dict[str, Any]:
    user = mesh_pb2.User.FromString(raw)
    return {
        "id": user.id or fallback_id,
        "longname": user.long_name,
        "shortname": user.short_name,
        "hardware": user.hw_model,
        "role": user.role,
    }


def _telemetry(raw: bytes) -> Dict[str, Any]:
    telemetry = telemetry_pb2.Telemetry.FromString(raw)
    out: Dict[str, Any] = {}
    variant = telemetry.WhichOneof("variant")
    if variant == "device_metrics":
        m = telemetry.device_metrics
        out = {
            "battery_level": m.battery_level,
            "voltage": m.voltage,
            "channel_utilization": m.channel_utilization,
            "air_util_tx": m.air_util_tx,
            "uptime_seconds": m.uptime_seconds,
        }
    elif variant == "environment_metrics":
        m = telemetry.environment_metrics
        out = {"temperature": m.temperature, "relative_humidity": m.relative_humidity, "barometric_pressure": m.barometric_pressure}
    return out


def _neighborinfo(raw: bytes) -> Dict[str, Any]:
    info = mesh_pb2.NeighborInfo.FromString(raw)
    return {
        "node_id": info.node_id,
        "last_sent_by_id": info.last_sent_by_id,
        "node_broadcast_interval_secs": info.node_broadcast_interval_secs,
        "neighbors": [{"node_id": n.node_id, "snr": n.snr} for n in info.neighbors],
    }


def _traceroute(raw: bytes, is_reply: bool) -> Dict[str, Any]:
    route = mesh_pb2.RouteDiscovery.FromString(raw)
    out: Dict[str, Any] = {"route": list(route.route), "snr_towards": list(route.snr_towards)}
    if is_reply:  # decode_traceroute() treats route_back / snr_back as the reply marker
        out["route_back"] = list(route.route_back)
        out["snr_back"] = list(route.snr_back)
    return out


def _map_report(raw: bytes) -> Dict[str, Any]:
    report = mqtt_pb2.MapReport.FromString(raw)
    return {
        "longname": report.long_name,
        "shortname": report.short_name,
        "role": report.role,
        "hardware": report.hw_model,
        "firmware_version": report.firmware_version,
        "latitude_i": report.latitude_i,
        "longitude_i": report.longitude_i,
        "altitude": report.altitude,
    }


def _base(packet, channel_id: str, gateway: str) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "from": getattr(packet, "from"),
        "to": packet.to,
        "id": packet.id,
        "channel": channel_id,
        "sender": gateway,
    }
    if packet.hop_start:
        message["hop_start"] = packet.hop_start
        message["hops_away"] = max(0, packet.hop_start - packet.hop_limit)
    if packet.rx_snr:
        message["snr"] = packet.rx_snr
    if packet.rx_rssi:
        message["rssi"] = packet.rx_rssi
    return message


def decode_service_envelope(raw: bytes, topic: str = "", keys: Iterable[str] = ("AQ==",)) -> List[Dict[str, Any]]:
    """Return zero or more JSON-style messages for one MQTT protobuf payload.

    keys: base64 channel keys to try, in order (the default "AQ==" key is always tried last).
    """
    try:
        envelope = mqtt_pb2.ServiceEnvelope.FromString(raw)
    except DecodeError:
        return []
    packet = envelope.packet
    if not envelope.HasField("packet"):
        return []

    gateway = format_node_id(envelope.gateway_id) if envelope.gateway_id else format_node_id(topic.rsplit("/", 1)[-1])
    base = _base(packet, envelope.channel_id, gateway)

    data = packet.decoded if packet.HasField("decoded") else None
    if data is None or data.portnum == PortNum.UNKNOWN_APP:
        data = None
        if packet.encrypted and not packet.pki_encrypted:
            for psk in [*keys, "AQ=="]:
                key = expand_psk(psk)
                if key and len(packet.encrypted):
                    data = _try_data(decrypt(packet.encrypted, key, packet.id, getattr(packet, "from")))
                    if data:
                        break
    if data is None:
        return [{**base, "type": "encrypted", "payload": {}}]

    from_id = format_node_id(getattr(packet, "from"))
    try:
        port = data.portnum
        if port == PortNum.TEXT_MESSAGE_APP:
            return [{**base, "type": "text", "payload": {"text": data.payload.decode("utf-8", "replace")}}]
        if port == PortNum.POSITION_APP:
            return [{**base, "type": "position", "payload": _position(data.payload)}]
        if port == PortNum.NODEINFO_APP:
            return [{**base, "type": "nodeinfo", "payload": _nodeinfo(data.payload, from_id)}]
        if port == PortNum.TELEMETRY_APP:
            return [{**base, "type": "telemetry", "payload": _telemetry(data.payload)}]
        if port == PortNum.NEIGHBORINFO_APP:
            return [{**base, "type": "neighborinfo", "payload": _neighborinfo(data.payload)}]
        if port == PortNum.TRACEROUTE_APP:
            return [{**base, "type": "traceroute", "payload": _traceroute(data.payload, bool(data.request_id))}]
        if port == PortNum.MAP_REPORT_APP:
            report = _map_report(data.payload)
            info = {"id": from_id, **{k: report[k] for k in ("longname", "shortname", "hardware", "role", "firmware_version")}}
            out = [{**base, "type": "nodeinfo", "payload": info}]
            if report["latitude_i"] or report["longitude_i"]:
                position = {k: report[k] for k in ("latitude_i", "longitude_i", "altitude") if report[k]}
                out.append({k: v for k, v in {**base, "type": "position", "payload": position}.items() if k != "id"})
            return out
        name = PortNum.Name(port).lower().removesuffix("_app")
        return [{**base, "type": name, "payload": {}}]
    except (DecodeError, ValueError):
        return [{**base, "type": "encrypted", "payload": {}}]
