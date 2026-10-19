import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from meshmon.meshlogic import build_gpx, decode_neighborinfo, decode_traceroute, distance_km, extract_links, extract_position, extract_telemetry, format_node_id


def test_node_id_padding():
    assert format_node_id(0x0ABC1234) == "!0abc1234"
    assert format_node_id("!ABCDEF01") == "!abcdef01"
    assert format_node_id("2882400018") == "!abcdef12"


def test_traceroute_request():
    msg = {"from": 1, "to": 4, "payload": {"route": [2, 3], "snr_towards": [8, -128, 20]}}
    [(direction, nodes, snrs)] = decode_traceroute(msg)
    assert direction == "towards"
    assert nodes == ["!00000001", "!00000002", "!00000003", "!00000004"]
    assert snrs == [2.0, None, 5.0]


def test_traceroute_reply_orientation():
    msg = {"from": 4, "to": 1, "payload": {"route": [2], "route_back": [3], "snr_towards": [4, 4], "snr_back": [-8, -8]}}
    results = decode_traceroute(msg)
    assert results[0][1] == ["!00000001", "!00000002", "!00000004"]
    assert results[1][1] == ["!00000004", "!00000003", "!00000001"]
    assert results[1][2] == [-2.0, -2.0]


def test_traceroute_snr_length_mismatch():
    msg = {"from": 1, "to": 2, "payload": {"route": [], "snr_towards": [1, 2, 3]}}
    assert decode_traceroute(msg)[0][2] == [None]


def test_neighborinfo_links():
    msg = {"type": "neighborinfo", "from": 9, "payload": {"node_id": 9, "neighbors": [{"node_id": 5, "snr": 6.5}, {"node_id": 6}]}}
    assert decode_neighborinfo(msg) == ("!00000009", [("!00000005", 6.5), ("!00000006", None)])
    assert extract_links(msg)[0] == ("!00000005", "!00000009", 6.5, "neighborinfo")


def test_bad_payloads():
    assert decode_traceroute({"from": 1, "to": 2, "payload": "x"}) == []
    assert decode_neighborinfo({"payload": None}) == (None, [])


def test_position_and_telemetry():
    pos = extract_position({"payload": {"latitude_i": 521000000, "longitude_i": 210000000, "altitude": 100}})
    assert abs(pos["lat"] - 52.1) < 1e-9 and abs(pos["lon"] - 21.0) < 1e-9 and pos["alt"] == 100.0
    assert extract_position({"payload": {"latitude_i": 0, "longitude_i": 0}}) is None
    assert extract_position({"payload": {}}) is None
    assert extract_telemetry({"payload": {"battery_level": 28, "voltage": 3.5, "x": 1}}) == {"battery": 28.0, "voltage": 3.5}
    assert extract_telemetry({"payload": {"x": 1}}) is None


def test_distance_and_gpx():
    assert abs(distance_km(52.2297, 21.0122, 50.0647, 19.9450) - 252) < 3  # Warsaw - Krakow
    gpx = build_gpx([("a & b", 1.0, 2.0, None)])
    assert "a &amp; b" in gpx and "<ele>" not in gpx
