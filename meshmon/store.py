"""SQLite log: raw packets (for replay), node last-seen, node info/position and telemetry history"""

import json
import sqlite3
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    topic TEXT,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS packets_ts ON packets(ts);
CREATE TABLE IF NOT EXISTS node_seen (
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- 'mesh' (originator) or 'gateway' (MQTT uplink)
    last_seen REAL NOT NULL,
    PRIMARY KEY (node_id, kind)
);
CREATE TABLE IF NOT EXISTS node_info (
    node_id TEXT PRIMARY KEY,
    role INTEGER,
    hardware INTEGER,
    lat REAL,
    lon REAL,
    alt REAL,
    pos_ts REAL
);
CREATE TABLE IF NOT EXISTS telemetry (
    node_id TEXT NOT NULL,
    ts REAL NOT NULL,
    battery REAL,
    voltage REAL,
    chan_util REAL,
    air_util REAL
);
CREATE INDEX IF NOT EXISTS telemetry_node_ts ON telemetry(node_id, ts);
"""


class Store:
    """Not thread-safe: use from one thread (open a second Store for other threads)"""

    def __init__(self, path: str, readonly: bool = False):
        self.path = path
        if readonly:
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        else:
            self.db = sqlite3.connect(path)
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def commit(self) -> None:
        self.db.commit()

    # --- writes ---

    def add_packet(self, ts: float, topic: str, payload: Dict[str, Any]) -> None:
        self.db.execute("INSERT INTO packets(ts, topic, raw) VALUES (?, ?, ?)", (ts, topic, json.dumps(payload)))

    def set_seen(self, node_id: str, kind: str, last_seen: float) -> None:
        self.db.execute(
            "INSERT INTO node_seen(node_id, kind, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(node_id, kind) DO UPDATE SET last_seen = MAX(last_seen, excluded.last_seen)",
            (node_id, kind, last_seen),
        )

    def set_node_info(self, node_id: str, info: Dict[str, Any]) -> None:
        """Upsert role / hardware / lat / lon / alt / pos_ts, keeping fields not supplied"""
        cols = [c for c in ("role", "hardware", "lat", "lon", "alt", "pos_ts") if c in info]
        if not cols:
            return
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
        self.db.execute(
            f"INSERT INTO node_info(node_id, {', '.join(cols)}) VALUES (?, {placeholders}) ON CONFLICT(node_id) DO UPDATE SET {updates}",
            (node_id, *[info[c] for c in cols]),
        )

    def add_telemetry(self, node_id: str, ts: float, battery=None, voltage=None, chan_util=None, air_util=None) -> None:
        self.db.execute(
            "INSERT INTO telemetry(node_id, ts, battery, voltage, chan_util, air_util) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, ts, battery, voltage, chan_util, air_util),
        )

    def prune(self, packet_days: float, node_days: float) -> None:
        now = time.time()
        self.db.execute("DELETE FROM packets WHERE ts < ?", (now - packet_days * 86400,))
        self.db.execute("DELETE FROM telemetry WHERE ts < ?", (now - packet_days * 86400,))
        self.db.execute("DELETE FROM node_seen WHERE last_seen < ?", (now - node_days * 86400,))
        self.db.commit()

    # --- reads ---

    def seen(self, kind: str) -> Dict[str, float]:
        rows = self.db.execute("SELECT node_id, last_seen FROM node_seen WHERE kind = ?", (kind,))
        return {r["node_id"]: r["last_seen"] for r in rows}

    def node_info(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for r in self.db.execute("SELECT * FROM node_info"):
            out[r["node_id"]] = {k: r[k] for k in r.keys() if k != "node_id" and r[k] is not None}
        return out

    def telemetry(self, since: float, limit_per_node: int = 300) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.db.execute("SELECT * FROM telemetry WHERE ts >= ? ORDER BY ts", (since,)):
            out.setdefault(r["node_id"], []).append({k: r[k] for k in r.keys() if k != "node_id"})
        return {n: rows[-limit_per_node:] for n, rows in out.items()}

    def recent_packets(self, limit: int) -> List[Tuple[float, str, Dict[str, Any]]]:
        rows = self.db.execute("SELECT ts, topic, raw FROM packets ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [(r["ts"], r["topic"], json.loads(r["raw"])) for r in reversed(rows)]

    def iter_packets(self, start: Optional[float] = None) -> Iterator[Tuple[float, str, Dict[str, Any]]]:
        query = "SELECT ts, topic, raw FROM packets" + (" WHERE ts >= ?" if start is not None else "") + " ORDER BY ts, id"
        for r in self.db.execute(query, (start,) if start is not None else ()):
            try:
                yield r["ts"], r["topic"], json.loads(r["raw"])
            except json.JSONDecodeError:
                continue

    def last_seen_of(self, node_id: str) -> Optional[float]:
        row = self.db.execute("SELECT MAX(last_seen) AS t FROM node_seen WHERE node_id = ?", (node_id,)).fetchone()
        return row["t"] if row else None
