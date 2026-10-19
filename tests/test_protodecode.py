import base64

from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

from meshmon.protodecode import DEFAULT_PSK, decrypt, decode_service_envelope, expand_psk

PORT = portnums_pb2.PortNum


def envelope(portnum, payload, encrypt_with=DEFAULT_PSK, request_id=0, channel="MediumFast"):
    packet = mesh_pb2.MeshPacket(to=0xFFFFFFFF, id=1234, hop_limit=3, hop_start=5, rx_snr=4.5)
    setattr(packet, "from", 0x970F5908)
    data = mesh_pb2.Data(portnum=portnum, payload=payload, request_id=request_id)
    if encrypt_with:
        packet.encrypted = decrypt(data.SerializeToString(), encrypt_with, 1234, 0x970F5908)  # CTR is symmetric
    else:
        packet.decoded.CopyFrom(data)
    return mqtt_pb2.ServiceEnvelope(packet=packet, channel_id=channel, gateway_id="!970f5908").SerializeToString()


def test_default_key():
    assert expand_psk("AQ==") == DEFAULT_PSK
    assert expand_psk("Ag==")[-1] == DEFAULT_PSK[-1] + 1
    assert expand_psk("AA==") is None


def test_encrypted_text_roundtrip():
    [msg] = decode_service_envelope(envelope(PORT.TEXT_MESSAGE_APP, "czesc [x]".encode()), "msh/PL/2/e/MediumFast/!970f5908")
    assert msg["type"] == "text" and msg["payload"]["text"] == "czesc [x]"
    assert msg["from"] == 0x970F5908 and msg["sender"] == "!970f5908" and msg["channel"] == "MediumFast"
    assert msg["hops_away"] == 2 and msg["snr"] == 4.5


def test_telemetry_position_nodeinfo():
    t = telemetry_pb2.Telemetry()
    t.device_metrics.battery_level = 55
    t.device_metrics.voltage = 3.9
    [msg] = decode_service_envelope(envelope(PORT.TELEMETRY_APP, t.SerializeToString()))
    assert msg["payload"]["battery_level"] == 55

    pos = mesh_pb2.Position(latitude_i=521000000, longitude_i=210000000, altitude=100)
    [msg] = decode_service_envelope(envelope(PORT.POSITION_APP, pos.SerializeToString()))
    assert msg["type"] == "position" and msg["payload"]["latitude_i"] == 521000000

    user = mesh_pb2.User(id="!970f5908", long_name="filips-07", short_name="flp7", hw_model=43, role=2)
    [msg] = decode_service_envelope(envelope(PORT.NODEINFO_APP, user.SerializeToString()))
    assert msg["payload"] == {"id": "!970f5908", "longname": "filips-07", "shortname": "flp7", "hardware": 43, "role": 2}


def test_wrong_key_is_marked_encrypted():
    other = bytes(range(16))
    [msg] = decode_service_envelope(envelope(PORT.TEXT_MESSAGE_APP, b"secret", encrypt_with=other))
    assert msg["type"] == "encrypted"
    custom = base64.b64encode(other).decode()
    [msg] = decode_service_envelope(envelope(PORT.TEXT_MESSAGE_APP, b"secret", encrypt_with=other), keys=[custom])
    assert msg["type"] == "text"


def test_traceroute_reply_marker():
    route = mesh_pb2.RouteDiscovery(route=[1, 2], snr_towards=[4, 4, 4])
    [req] = decode_service_envelope(envelope(PORT.TRACEROUTE_APP, route.SerializeToString()))
    [rep] = decode_service_envelope(envelope(PORT.TRACEROUTE_APP, route.SerializeToString(), request_id=7))
    assert "route_back" not in req["payload"] and "route_back" in rep["payload"]


def test_map_report_gives_nodeinfo_and_position():
    report = mqtt_pb2.MapReport(long_name="filips-05", short_name="flp5", role=2, hw_model=43, firmware_version="2.7.2", latitude_i=521000000, longitude_i=210000000)
    msgs = decode_service_envelope(envelope(PORT.MAP_REPORT_APP, report.SerializeToString(), encrypt_with=None), "msh/PL/2/map/")
    assert [m["type"] for m in msgs] == ["nodeinfo", "position"]
    assert msgs[0]["payload"]["longname"] == "filips-05" and "id" not in msgs[1]


def test_garbage():
    assert decode_service_envelope(b"\xff\xfe garbage") == [] or decode_service_envelope(b"\xff\xfe garbage")[0]["type"] == "encrypted"
