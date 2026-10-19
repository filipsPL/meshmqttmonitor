import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from meshmon.store import Store


def test_roundtrip(tmp_path):
    s = Store(str(tmp_path / "t.sqlite"))
    now = time.time()
    s.add_packet(now - 2, "t", {"a": 1})
    s.add_packet(now - 1, "t", {"a": 2})
    s.set_seen("!1", "mesh", 10)
    s.set_seen("!1", "mesh", 5)  # older value must not win
    s.set_node_info("!1", {"role": 2})
    s.set_node_info("!1", {"lat": 1.5, "lon": 2.5, "pos_ts": 3})  # keeps role
    s.add_telemetry("!1", now, battery=50)
    s.commit()
    assert [p[2]["a"] for p in s.recent_packets(10)] == [1, 2]
    assert s.seen("mesh") == {"!1": 10}
    assert s.node_info()["!1"] == {"role": 2, "lat": 1.5, "lon": 2.5, "pos_ts": 3}
    assert s.telemetry(now - 1)["!1"][0]["battery"] == 50
    s.prune(packet_days=0, node_days=0)
    assert s.recent_packets(10) == [] and s.seen("mesh") == {}
    s.close()


def test_readonly_replay(tmp_path):
    path = str(tmp_path / "t.sqlite")
    s = Store(path)
    s.add_packet(1.0, "t", {"x": 1})
    s.close()
    ro = Store(path, readonly=True)
    assert [p[2] for p in ro.iter_packets()] == [{"x": 1}]
