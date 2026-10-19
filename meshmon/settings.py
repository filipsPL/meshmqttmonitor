"""Configuration: loads the user's config file (default config.py, or --config / MESH_CONFIG) with defaults"""

import argparse
import importlib.util
import os
import sys

# Project root (config.py, nodes_db.pkl and mesh_log.sqlite live next to meshmqttmonitor2.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Meshtastic MQTT console monitor", allow_abbrev=False)
    parser.add_argument("--config", default=os.environ.get("MESH_CONFIG", os.path.join(BASE_DIR, "config.py")), help="config file (default: config.py)")
    parser.add_argument("--topic", action="append", help="MQTT topic to subscribe to (repeatable; overrides config)")
    parser.add_argument("--db", help="SQLite log file (default: mesh_log.sqlite next to the script)")
    parser.add_argument("--no-log", action="store_true", help="do not write the SQLite log")
    parser.add_argument("--replay", metavar="DB", help="replay packets from a SQLite log instead of connecting to MQTT")
    parser.add_argument("--speed", type=float, default=10.0, help="replay speed multiplier (default: 10)")
    args, _unknown = parser.parse_known_args(argv)
    return args


ARGS = _parse_args(sys.argv[1:])

_spec = importlib.util.spec_from_file_location("mesh_user_config", ARGS.config)
if _spec is None or _spec.loader is None or not os.path.exists(ARGS.config):
    raise SystemExit(f"Config file not found: {ARGS.config} (copy config.example.py to config.py)")
_cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cfg)


def _get(name, default):
    return getattr(_cfg, name, default)


# Required
MQTT_BROKER = _cfg.MQTT_BROKER
MQTT_PORT = _cfg.MQTT_PORT
MQTT_USERNAME = _cfg.MQTT_USERNAME
MQTT_PASSWORD = _cfg.MQTT_PASSWORD


def _split_nodes(nodes):
    """NODES = {"!id": ("friendly name", "color")} (or just "friendly name") -> (ALIAS_MAP, COLOR_MAP)"""
    aliases, colors = {}, {}
    for node_id, entry in nodes.items():
        name, color = (entry, None) if isinstance(entry, str) else (tuple(entry) + (None,))[:2]
        aliases[node_id] = name
        if color:
            colors[name] = color
    return aliases, colors


# Preferred: NODES. The legacy ALIAS_MAP / COLOR_MAP are still read and merged underneath it.
_nodes_aliases, _nodes_colors = _split_nodes(_get("NODES", {}))
ALIAS_MAP = {**_get("ALIAS_MAP", {}), **_nodes_aliases}
COLOR_MAP = {**_get("COLOR_MAP", {}), **_nodes_colors}
CHART_WIDTH = _get("CHART_WIDTH", 200)

# Optional
MQTT_TOPICS = list(ARGS.topic or _get("MQTT_TOPICS", None) or [_cfg.MQTT_TOPIC])

# Protobuf feed: the binary topics next to a ".../json/..." topic ("/e/" encrypted packets, "/map/" map reports)
DECODE_PROTOBUF = _get("DECODE_PROTOBUF", True)
if DECODE_PROTOBUF:
    for _topic in list(MQTT_TOPICS):
        if "/json/" in _topic:
            _root = _topic.split("/json/")[0]
            for _extra in (f"{_root}/e/#", f"{_root}/map/#"):
                if _extra not in MQTT_TOPICS:
                    MQTT_TOPICS.append(_extra)
CHANNEL_KEYS = _get("CHANNEL_KEYS", {})  # channel name -> base64 key (the default "AQ==" key is always tried)
MQTT_TOPIC = ", ".join(MQTT_TOPICS)
CLIENT_ID = _get("CLIENT_ID", None)
USE_SSL = _get("USE_SSL", True)
TLS_INSECURE = _get("TLS_INSECURE", False)  # True only for self-signed broker certificates

NODES_DB_PATH = os.path.join(BASE_DIR, "nodes_db.pkl")
DB_PATH = ARGS.db or _get("DB_PATH", os.path.join(BASE_DIR, "mesh_log.sqlite"))
LOG_ENABLED = not ARGS.no_log and not ARGS.replay
LOG_RETENTION_DAYS = _get("LOG_RETENTION_DAYS", 14)
NODE_RETENTION_DAYS = _get("NODE_RETENTION_DAYS", 7)

HOME_POSITION = _get("HOME_POSITION", None)  # (lat, lon) in degrees, for distances
WATCH_NODES = _get("WATCH_NODES", list(ALIAS_MAP.keys() - {"!ffffffff"}))  # alerts for these node IDs
LOW_BATTERY_PERCENT = _get("LOW_BATTERY_PERCENT", 20)
OFFLINE_MINUTES = _get("OFFLINE_MINUTES", 30)
MENTION_WORDS = _get("MENTION_WORDS", [])  # words in text messages that raise a notification
