#!/usr/bin/env python3
"""
MeshTextualMonitor - A Textual-based UI for monitoring Meshtastic MQTT messages
"""

import ssl
import json
import pickle
import os
import queue
import tempfile
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime
from typing import Dict, Any, List, Tuple

import paho.mqtt.client as mqtt
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Container  # , Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, Input
from textual.binding import Binding

from meshmon.meshlogic import build_gpx, extract_links, extract_position, extract_telemetry, format_node_id
from meshmon.protodecode import decode_service_envelope
from meshmon.store import Store
from meshmon.widgets import (
    ChatScreen,
    DebugWidget,
    LinksScreen,
    MessageRateMonitor,
    MessagesWidget,
    NodeDetailScreen,
    NodeListWidget,
)
from meshmon.settings import (
    ALIAS_MAP,
    ARGS,
    BASE_DIR,
    CHANNEL_KEYS,
    CHART_WIDTH,
    CLIENT_ID,
    COLOR_MAP,
    DB_PATH,
    HOME_POSITION,
    LOG_ENABLED,
    LOG_RETENTION_DAYS,
    LOW_BATTERY_PERCENT,
    MENTION_WORDS,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TOPIC,
    MQTT_TOPICS,
    MQTT_USERNAME,
    NODE_RETENTION_DAYS,
    NODES_DB_PATH,
    OFFLINE_MINUTES,
    TLS_INSECURE,
    USE_SSL,
    WATCH_NODES,
)

RESTORE_PACKETS = 500  # recent packets replayed into the tables at startup


class MeshTextualMonitor(App):
    """A Textual app for monitoring Meshtastic MQTT messages."""

    CSS_PATH = "styles.tcss"

    TITLE = "Meshtastic Textual Monitor"
    SUB_TITLE = f"REPLAY {ARGS.replay}" if ARGS.replay else f"MQTT Topic: {MQTT_TOPIC}"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("c", "clear_data", "Clear Data"),
        Binding("d", "toggle_debug", "Toggle Debug"),
        Binding("n", "show_links", "Links"),
        Binding("h", "show_chat", "Chat"),
        Binding("/", "start_search", "Search"),
        Binding("t", "cycle_type", "Type filter"),
        Binding("x", "clear_filters", "Clear filters"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("g", "export_gpx", "Export GPX"),
        Binding("escape", "cancel_search", "Cancel", show=False),
    ]

    MAX_MESSAGES = 200
    DEDUP_WINDOW = 600  # seconds a packet id is remembered
    DEDUP_MAX_ENTRIES = 5000

    def __init__(self):
        super().__init__()
        self.node_data = {}  # node_id -> {msg_type -> message}
        self.nodes_db = self.load_nodes_db()
        self.nodes_db_dirty = False
        self.last_update_time = None
        self.alias_map_with_new = {**self.nodes_db, **ALIAS_MAP}
        self.connected = False
        self.last_connect_error = None
        self.debug_mode = True  # Set to True by default
        self.mqtt_nodes = {}
        self.mesh_nodes = {}
        self.stored_mesh_nodes = {}
        self.all_messages = []
        self.duplicate_count = 0  # copies of an already-seen packet heard via another gateway
        self.seen_packets: "OrderedDict[Tuple[Any, Any], Dict[str, Any]]" = OrderedDict()
        self.gateway_last_seen: Dict[str, float] = {}
        self.node_info: Dict[str, Dict[str, Any]] = {}  # node_id -> role/hardware from nodeinfo
        # (transmitter, receiver) -> {"snr", "timestamp", "source"}: receiver heard transmitter
        self.links: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.total_message_count = 0  # Counter for total messages received, independent of message list

        self.home = HOME_POSITION
        self.telemetry: Dict[str, deque] = {}  # node_id -> recent device-metric samples
        self.chat: deque = deque(maxlen=500)
        self.chat_total = 0
        self.recent_types: deque = deque()  # (timestamp, type) of unique messages, last 60 s
        self.search_text = ""
        self.type_filter = ""  # "" = all
        self.paused = False
        self.frozen_messages_rendered = False
        self.frozen_messages: List[Dict[str, Any]] = []
        self.restoring = False  # replaying stored packets at startup: no logging / alerts
        self.low_battery_alerted: set = set()
        self.offline_alerted: set = set()
        self.last_persist = 0.0
        self._replay_stop = threading.Event()

        self.store = None

        # The MQTT client runs callbacks on its own thread. They only push onto these
        # thread-safe queues; all state and widget changes happen on the UI thread.
        self._ui_thread_id = threading.get_ident()
        self._incoming: "queue.SimpleQueue[Tuple[str, Dict[str, Any]]]" = queue.SimpleQueue()
        self._debug_queue: "queue.SimpleQueue[str]" = queue.SimpleQueue()

        self.init_store()

        # MQTT client setup
        self.setup_mqtt_client()

    def init_store(self) -> None:
        """Open the SQLite log and restore last-seen times, node info, telemetry and recent packets"""
        if not LOG_ENABLED:
            return
        try:
            self.store = Store(DB_PATH)
            self.store.prune(LOG_RETENTION_DAYS, NODE_RETENTION_DAYS)
            self.stored_mesh_nodes = self.store.seen("mesh")
            self.gateway_last_seen = self.store.seen("gateway")
            self.node_info = self.store.node_info()
            for node_id, rows in self.store.telemetry(time.time() - 86400).items():
                self.telemetry[node_id] = deque(rows, maxlen=300)
            self.restoring = True
            try:
                for ts, topic, payload in self.store.recent_packets(RESTORE_PACKETS):
                    self.process_message(payload, topic, now=ts)
            finally:
                self.restoring = False
            self.total_message_count = 0
            self.duplicate_count = 0
        except Exception as e:
            print(f"Could not open log {DB_PATH}: {e}")
            self.store = None

    def persist_state(self) -> None:
        """Write last-seen times to the SQLite log"""
        if not self.store:
            return
        try:
            for node_id, ts in self.mesh_nodes.items():
                self.store.set_seen(node_id, "mesh", ts)
            for node_id, ts in self.gateway_last_seen.items():
                self.store.set_seen(node_id, "gateway", ts)
            self.store.commit()
            self.last_persist = time.time()
        except Exception as e:
            self.log_debug(f"Error persisting state: {e}")

    def start_replay(self) -> None:
        """Feed packets from a SQLite log into the normal pipeline, at ARGS.speed times real time"""

        def run():
            try:
                source = Store(ARGS.replay, readonly=True)
                previous = None
                for ts, topic, payload in source.iter_packets():
                    if self._replay_stop.is_set():
                        return
                    if previous is not None:
                        # cap the wait so long idle gaps do not stall the replay
                        self._replay_stop.wait(min((ts - previous) / max(ARGS.speed, 0.01), 2.0))
                    previous = ts
                    self._incoming.put((topic, payload))
                self.log_debug("Replay finished")
            except Exception as e:
                self.log_debug(f"Replay error: {e}")

        threading.Thread(target=run, daemon=True, name="replay").start()

    def setup_mqtt_client(self):
        """Set up the MQTT client"""
        kwargs = {"callback_api_version": mqtt.CallbackAPIVersion.VERSION2}
        if CLIENT_ID:
            kwargs["client_id"] = CLIENT_ID
        self.mqtt_client = mqtt.Client(**kwargs)

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        self.mqtt_client.on_message = self.on_mqtt_message

        # Set username and password
        self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        if USE_SSL:
            if TLS_INSECURE:
                self.mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
                self.mqtt_client.tls_insecure_set(True)
            else:
                self.mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        # Set reconnection delay
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

    # --- MQTT callbacks (paho thread: no widget access here) ---

    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        """Callback when MQTT client connects"""
        self.connected = not reason_code.is_failure
        if self.connected:
            self.last_connect_error = None
            client.subscribe([(topic, 0) for topic in MQTT_TOPICS])
            self.log_debug(f"Subscribed to {MQTT_TOPIC}")
        else:
            self.last_connect_error = str(reason_code)
            self.log_debug(f"Connection failed: {reason_code}")

    def on_mqtt_disconnect(self, client, userdata, flags, reason_code, properties=None):
        """Callback when MQTT client disconnects"""
        self.connected = False
        self.log_debug(f"Disconnected from MQTT Broker ({reason_code})")

    def on_mqtt_message(self, client, userdata, msg):
        """Callback when a message is received: decode and enqueue for the UI thread"""
        try:
            if "/json/" in msg.topic or msg.payload.lstrip()[:1] == b"{":  # protobuf envelopes never start with "{"
                raw_payload = msg.payload.decode("utf-8", "ignore")
                self.log_debug(f"MQTT {msg.topic}: {raw_payload}")
                payload = json.loads(raw_payload)
                messages = [payload] if isinstance(payload, dict) else []
            else:
                channel = msg.topic.partition("/2/e/")[2].split("/")[0]  # root may have any depth: <root>/2/e/<channel>/<gw>
                keys = CHANNEL_KEYS.get(channel, [])
                keys = [keys] if isinstance(keys, str) else list(keys)
                messages = decode_service_envelope(msg.payload, msg.topic, keys)
                self.log_debug(f"MQTT {msg.topic}: {len(msg.payload)} bytes -> {', '.join(m['type'] for m in messages) or 'undecodable'}")
            for payload in messages:
                self._incoming.put((msg.topic, payload))
        except json.JSONDecodeError as e:
            self.log_debug(f"JSON decode error: {e}")
        except Exception as e:
            self.log_debug(f"Error processing message: {e}")

    def log_debug(self, message):
        """Log debug information if debug mode is enabled (safe from any thread)"""
        if not self.debug_mode:
            return
        if threading.get_ident() == self._ui_thread_id:
            self._write_debug(message)
        else:
            self._debug_queue.put(message)

    def _write_debug(self, message: str, markup: bool = False) -> None:
        try:
            self.query_one("#debug_panel", DebugWidget).add_message(message, markup=markup)
        except Exception:
            pass  # widget not mounted yet or app shutting down

    # --- UI-thread processing ---

    def drain_queues(self) -> None:
        """Move data queued by the MQTT thread into app state (UI thread only)"""
        while True:
            try:
                self._write_debug(self._debug_queue.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                topic, payload = self._incoming.get_nowait()
            except queue.Empty:
                break
            try:
                if self.store:
                    self.store.add_packet(time.time(), topic, payload)  # raw, before processing mutates it
                self.process_message(payload, topic)
            except Exception as e:
                self.log_debug(f"Error processing message: {e}")

    def process_message(self, payload: Dict[str, Any], topic: str = "", now: float = None) -> None:
        msg_type = str(payload.get("type", "unknown msg type"))
        node_id = format_node_id(payload.get("sender", "unknown sender"))

        now = time.time() if now is None else now
        self.gateway_last_seen[node_id] = now

        # The same packet is uplinked by every gateway that heard it: merge copies
        packet_key = (payload.get("from"), payload.get("id"))
        if None not in packet_key:
            first = self.seen_packets.get(packet_key)
            if first is not None and now - first["timestamp"] < self.DEDUP_WINDOW:
                self.duplicate_count += 1
                if node_id not in first["gateways"]:
                    first["gateways"].append(node_id)
                # Keep the best reception seen
                if isinstance(payload.get("snr"), (int, float)) and payload["snr"] > first.get("snr", float("-inf")):
                    for field in ("snr", "rssi", "hops_away", "hop_start"):
                        if field in payload:
                            first[field] = payload[field]
                return
            self.seen_packets[packet_key] = payload
            while len(self.seen_packets) > self.DEDUP_MAX_ENTRIES:
                self.seen_packets.popitem(last=False)
        payload["gateways"] = [node_id]

        # Use receive time: device clocks are often unset or wrong
        if "timestamp" in payload:
            payload["device_timestamp"] = payload["timestamp"]
        payload["timestamp"] = now

        self.node_data.setdefault(node_id, {})[msg_type] = payload
        self.last_update_time = datetime.now()

        if msg_type.lower() == "nodeinfo" and isinstance(payload.get("payload"), dict):
            self.update_nodes_db({"nodeinfo": payload})

        for tx, rx, snr, source in extract_links(payload):
            self.links[(tx, rx)] = {"snr": snr, "timestamp": now, "source": source}

        self.record_node_data(payload, msg_type.lower(), now)

        self.all_messages.append(payload)
        if len(self.all_messages) > self.MAX_MESSAGES:
            self.all_messages = self.all_messages[-self.MAX_MESSAGES :]
        self.total_message_count += 1
        if not self.restoring:
            self.recent_types.append((now, msg_type.lower()))

    def record_node_data(self, payload: Dict[str, Any], msg_type: str, now: float) -> None:
        """Position, telemetry history, chat and alerts derived from one unique message"""
        origin = format_node_id(payload.get("from", payload.get("sender")))

        position = extract_position(payload)
        if position:
            update = {"lat": position["lat"], "lon": position["lon"], "pos_ts": now}
            if "alt" in position:
                update["alt"] = position["alt"]
            self.node_info.setdefault(origin, {}).update(update)
            if self.store and not self.restoring:
                self.store.set_node_info(origin, update)

        if msg_type == "telemetry" and not self.restoring:
            metrics = extract_telemetry(payload)
            if metrics:
                self.telemetry.setdefault(origin, deque(maxlen=300)).append({"ts": now, **metrics})
                if self.store:
                    self.store.add_telemetry(origin, now, **metrics)
                self.check_battery(origin, metrics.get("battery"))

        if msg_type == "text" and isinstance(payload.get("payload"), dict) and "text" in payload["payload"]:
            text = str(payload["payload"]["text"])
            self.chat.append({"ts": now, "origin": origin, "channel": payload.get("channel", ""), "text": text})
            self.chat_total += 1
            if not self.restoring and any(w.lower() in text.lower() for w in MENTION_WORDS):
                self.notify(text, title=f"Mention from {self.alias_map_with_new.get(origin, origin)}", timeout=15)

    def check_battery(self, node_id: str, battery) -> None:
        if battery is None or battery > 100 or node_id not in WATCH_NODES:
            return  # >100 means external power
        name = self.alias_map_with_new.get(node_id, node_id)
        if battery <= LOW_BATTERY_PERCENT and node_id not in self.low_battery_alerted:
            self.low_battery_alerted.add(node_id)
            self.notify(f"{name}: battery {battery:.0f}%", title="Low battery", severity="warning", timeout=15)
            self.log_debug(f"Low battery: {name} {battery:.0f}%")
        elif battery > LOW_BATTERY_PERCENT + 5:
            self.low_battery_alerted.discard(node_id)

    def check_offline(self) -> None:
        """Alert once when a watched node that was heard goes quiet"""
        if ARGS.replay:
            return
        now = time.time()
        for node_id in WATCH_NODES:
            last = self.mesh_nodes.get(node_id)
            if last is None:
                continue
            name = self.alias_map_with_new.get(node_id, node_id)
            if now - last > OFFLINE_MINUTES * 60:
                if node_id not in self.offline_alerted:
                    self.offline_alerted.add(node_id)
                    self.notify(f"{name} silent for {OFFLINE_MINUTES}+ min", title="Node offline", severity="warning", timeout=15)
            else:
                self.offline_alerted.discard(node_id)

    def update_nodes_db(self, messages: Dict[str, Dict[str, Any]]):
        """Update the nodes database with nodeinfo data"""
        try:
            nodeinfo = messages.get("nodeinfo")
            if not nodeinfo:
                return
            payload = nodeinfo.get("payload")
            if not isinstance(payload, dict):
                return

            # Meshtastic JSON uses "id"; fall back to "node_id" and the sender
            node_id = payload.get("node_id") or payload.get("id") or nodeinfo.get("sender")
            longname = payload.get("longname")
            if node_id:
                info = {k: payload[k] for k in ("role", "hardware") if k in payload}
                if info:
                    self.node_info.setdefault(format_node_id(node_id), {}).update(info)
                    if self.store and not self.restoring:
                        self.store.set_node_info(format_node_id(node_id), info)
            if not node_id or not isinstance(longname, str) or not longname.strip():
                return

            name = longname.strip()
            shortname = payload.get("shortname")
            # Same "Long (Short)" format as import_nodes_liam.py
            if isinstance(shortname, str) and shortname.strip() and shortname.strip() != name:
                name = f"{name} ({shortname.strip()})"

            node_id_hex = format_node_id(node_id)
            if self.nodes_db.get(node_id_hex) != name:
                self.nodes_db[node_id_hex] = name
                self.nodes_db_dirty = True
                self.alias_map_with_new = {**self.nodes_db, **ALIAS_MAP}
                self.log_debug(f"Updated nodes_db: {node_id_hex} -> {name}")
        except Exception as e:
            self.log_debug(f"Error updating nodes_db: {e}")

    def save_nodes_db(self) -> None:
        """Atomically save the nodes database"""
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(NODES_DB_PATH), suffix=".tmp")
            with os.fdopen(fd, "wb") as file:
                pickle.dump(self.nodes_db, file)
            os.replace(tmp_path, NODES_DB_PATH)
            self.nodes_db_dirty = False
            self.log_debug(f"Saved nodes_db with {len(self.nodes_db)} entries")
        except Exception as e:
            self.log_debug(f"Error saving nodes_db: {e}")
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def extract_node_data(self):
        """Extract MQTT and Mesh node data from messages"""
        mqtt_nodes = {}
        mesh_nodes = {}

        for node_id, messages in self.node_data.items():
            for content in messages.values():
                timestamp = content.get("timestamp", 0)

                # Keep the NEWEST timestamp per node
                mqtt_nodes[node_id] = max(mqtt_nodes.get(node_id, 0), timestamp)

                # Extract mesh node info from payload
                if isinstance(content.get("payload"), dict) and "node_id" in content["payload"]:
                    mesh_node_str = format_node_id(content["payload"]["node_id"])
                    mesh_nodes[mesh_node_str] = max(mesh_nodes.get(mesh_node_str, 0), timestamp)

                # Also check 'from' field which might indicate mesh node
                if "from" in content:
                    mesh_from_str = format_node_id(content["from"])
                    mesh_nodes[mesh_from_str] = max(mesh_nodes.get(mesh_from_str, 0), timestamp)

        # A gateway is alive whenever it uplinks anything, even duplicate copies
        for gw_id, timestamp in self.gateway_last_seen.items():
            mqtt_nodes[gw_id] = max(mqtt_nodes.get(gw_id, 0), timestamp)

        # Merge with stored mesh nodes - keep the most recent timestamp for each node
        for node_id, timestamp in self.stored_mesh_nodes.items():
            if node_id not in mesh_nodes or mesh_nodes[node_id] < timestamp:
                mesh_nodes[node_id] = timestamp

        self.mqtt_nodes = mqtt_nodes
        self.mesh_nodes = mesh_nodes
        self.stored_mesh_nodes = mesh_nodes.copy()

    def update_node_widgets(self):
        """Update the node list widgets with current data"""
        for table_id, nodes, label in (
            ("#table_mqtt_nodes", self.mqtt_nodes, "MQTT"),
            ("#table_mesh_nodes", self.mesh_nodes, "Mesh"),
        ):
            try:
                widget = self.query_one(table_id).parent
                if isinstance(widget, NodeListWidget):
                    widget.update_nodes(nodes, self.alias_map_with_new, COLOR_MAP, self.node_info, self.home)
            except Exception as e:
                self.log_debug(f"Error updating {label} nodes: {e}")

    def color_for(self, node_id: str) -> str:
        return COLOR_MAP.get(self.alias_map_with_new.get(node_id, node_id), "white")

    def message_matches(self, message: Dict[str, Any]) -> bool:
        if self.type_filter and str(message.get("type", "")).lower() != self.type_filter:
            return False
        if self.search_text:
            origin = format_node_id(message.get("from", message.get("sender")))
            gateways = [format_node_id(g) for g in message.get("gateways", [])]
            names = [self.alias_map_with_new.get(n, n) for n in [origin, *gateways]]
            haystack = " ".join(
                [origin, str(message.get("type", "")), *names, json.dumps(message.get("payload", ""), ensure_ascii=False)]
            ).lower()
            return self.search_text.lower() in haystack
        return True

    def filter_summary(self) -> str:
        parts = []
        if self.type_filter:
            parts.append(f"type={self.type_filter}")
        if self.search_text:
            parts.append(f"search='{self.search_text}'")
        text = f"[yellow]filter: {escape(', '.join(parts))}[/yellow]" if parts else ""
        if self.paused:
            text += " [red]PAUSED[/red]"
        return text

    def update_messages_widget(self):
        """Update the messages widget with current data"""
        try:
            messages_widget = self.query_one(MessagesWidget)
            messages_widget.set_title(self.filter_summary())
            source = self.frozen_messages if self.paused else self.all_messages
            messages = [m for m in source if self.message_matches(m)]
            if not self.paused or not self.frozen_messages_rendered:
                messages_widget.update_messages(messages, self.alias_map_with_new, COLOR_MAP)
                self.frozen_messages_rendered = self.paused
        except Exception as e:
            self.log_debug(f"Error updating messages widget: {e}")

    def action_reconnect(self) -> None:
        """Reconnect to MQTT broker"""
        try:
            self.mqtt_client.reconnect()
        except Exception:
            # reconnect() fails if the client never connected; start a fresh async connect
            try:
                self.mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
            except Exception as e:
                self.last_connect_error = str(e)
                self.log_debug(f"Reconnection failed: {e}")
                return
        self.log_debug("Reconnect attempt initiated")

    def action_clear_data(self) -> None:
        """Clear all message data"""
        self.node_data = {}
        self.mqtt_nodes = {}
        self.mesh_nodes = {}
        self.stored_mesh_nodes = {}
        self.gateway_last_seen = {}
        self.links = {}
        self.seen_packets.clear()
        self.all_messages = []
        self.frozen_messages = []
        self.telemetry = {}
        self.chat.clear()
        self.recent_types.clear()
        # We don't reset the total_message_count here as we want to keep statistics
        self.log_debug("Data cleared by user")

        # Clear debug log
        try:
            self.query_one("#debug_panel", DebugWidget).clear_messages()
        except Exception as e:
            print(f"Error clearing debug panel: {e}")

        self.update_ui()

    def action_show_chat(self) -> None:
        self.push_screen(ChatScreen())

    def action_start_search(self) -> None:
        box = self.query_one("#search_box", Input)
        box.add_class("visible")
        box.value = self.search_text
        box.focus()

    def action_cancel_search(self) -> None:
        box = self.query_one("#search_box", Input)
        if box.has_class("visible"):
            box.remove_class("visible")
            self.query_one("#messages_table", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search_box":
            self.search_text = event.value.strip()
            self.frozen_messages_rendered = False
            self.action_cancel_search()
            self.update_messages_widget()

    def action_cycle_type(self) -> None:
        types = sorted({str(m.get("type", "")).lower() for m in self.all_messages if m.get("type")})
        cycle = [""] + types
        current = cycle.index(self.type_filter) if self.type_filter in cycle else 0
        self.type_filter = cycle[(current + 1) % len(cycle)]
        self.frozen_messages_rendered = False
        self.update_messages_widget()

    def action_clear_filters(self) -> None:
        self.search_text = ""
        self.type_filter = ""
        self.frozen_messages_rendered = False
        self.update_messages_widget()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.frozen_messages = list(self.all_messages)
        self.frozen_messages_rendered = False
        self.update_messages_widget()

    def action_export_gpx(self) -> None:
        waypoints = [
            (self.alias_map_with_new.get(node_id, node_id), info["lat"], info["lon"], info.get("alt"))
            for node_id, info in self.node_info.items()
            if "lat" in info and "lon" in info
        ]
        if not waypoints:
            self.notify("No node positions known yet", severity="warning")
            return
        path = os.path.join(BASE_DIR, f"positions_{datetime.now():%Y%m%d_%H%M%S}.gpx")
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(build_gpx(waypoints))
            self.notify(f"{len(waypoints)} positions written to {path}", title="GPX export")
        except OSError as e:
            self.notify(f"Export failed: {e}", severity="error")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a node row opens its detail screen"""
        table_id = event.data_table.id or ""
        if table_id.startswith("table_") and event.row_key.value:
            self.push_screen(NodeDetailScreen(str(event.row_key.value)))

    def action_show_links(self) -> None:
        """Show the radio links learned from neighbourinfo and traceroute"""
        self.push_screen(LinksScreen())

    def action_toggle_debug(self) -> None:
        """Toggle debug mode"""
        self.debug_mode = not self.debug_mode
        state = "enabled" if self.debug_mode else "disabled"
        color = "green" if self.debug_mode else "yellow"
        self._write_debug(f"[{color}]Debug mode {state}[/{color}]", markup=True)
        self.notify(f"Debug mode {state}", title="Debug")

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header()
        yield Static("[yellow]Initializing...[/yellow]", id="connection_status")

        with Container(id="main_container"):
            # First row - Three columns
            with Container(id="left_panel"):
                yield NodeListWidget("MQTT Nodes", classes="node_list")

            with Container(id="center_panel"):
                yield NodeListWidget("Mesh Nodes", classes="node_list")

            with Container(id="right_panel"):
                yield DebugWidget(id="debug_panel")

            # Second row - Sparkline (spans all columns)
            with Container(id="sparkline_row"):
                yield MessageRateMonitor(chart_width=CHART_WIDTH)

            # Third row - Messages list (spans all columns)
            with Container(id="messages_panel"):
                yield Input(placeholder="search sender, type, text… (Enter to apply, Esc to cancel)", id="search_box")
                yield MessagesWidget()

        yield Footer()

    def on_mount(self) -> None:
        """Set up app when mounted"""
        self._ui_thread_id = threading.get_ident()
        if ARGS.replay:
            self.start_replay()
        else:
            try:
                self.mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
                self.mqtt_client.loop_start()
            except Exception as e:
                self.last_connect_error = str(e)
                self.log_debug(f"Connection failed: {e}")

        # Set up periodic UI updates (every 1 second)
        self.set_interval(1, self.update_ui)

    def update_ui(self) -> None:
        """Update the UI with current data"""
        self.drain_queues()

        # Update connection status with timestamp
        try:
            status = self.query_one("#connection_status", Static)
            if ARGS.replay:
                status.update(f"[cyan]● Replaying {escape(ARGS.replay)} at {ARGS.speed:g}x[/cyan]")
            elif self.connected:
                last_update = f" (Last update: {self.last_update_time.strftime('%H:%M:%S')})" if self.last_update_time else ""
                status.update(f"[green]● Connected to MQTT Broker{escape(last_update)}[/green]")
            elif self.last_connect_error:
                status.update(f"[red]● Disconnected: {escape(self.last_connect_error)}[/red]")
            else:
                status.update("[red]● Disconnected from MQTT Broker[/red]")
        except Exception as e:
            self.log_debug(f"Error updating status: {e}")

        if self.nodes_db_dirty:
            self.save_nodes_db()

        self.alias_map_with_new = {**self.nodes_db, **ALIAS_MAP}
        self.extract_node_data()
        self.update_node_widgets()
        self.update_messages_widget()

        now = time.time()
        while self.recent_types and self.recent_types[0][0] < now - 60:
            self.recent_types.popleft()
        type_summary = " · ".join(f"{t} {n}" for t, n in Counter(t for _, t in self.recent_types).most_common(8))

        if now - self.last_persist > 30:
            self.persist_state()
            self.check_offline()
            self.last_persist = now

        try:
            self.query_one(MessageRateMonitor).update_with_count(self.total_message_count, self.duplicate_count, type_summary)
        except Exception as e:
            self.log_debug(f"Error updating message rate monitor: {e}")
            # Try to recreate the monitor
            try:
                sparkline_row = self.query_one("#sparkline_row")
                sparkline_row.remove_children()
                sparkline_row.mount(MessageRateMonitor(chart_width=CHART_WIDTH))
                self.log_debug("Rate monitor recreated after error")
            except Exception as re:
                self.log_debug(f"Error recreating rate monitor: {re}")

    def load_nodes_db(self, file_path=NODES_DB_PATH) -> Dict[str, str]:
        """Load the nodes_db dictionary from a pickle file"""
        if os.path.exists(file_path):
            try:
                with open(file_path, "rb") as file:
                    nodes_db = pickle.load(file)
                if isinstance(nodes_db, dict):
                    # Re-normalise keys (older files may hold unpadded IDs)
                    return {format_node_id(k): v for k, v in nodes_db.items()}
                print("nodes_db file has unexpected format, ignoring")
            except Exception as e:
                print(f"Error loading nodes_db from file: {e}")

        return {}

    def on_unmount(self) -> None:
        """Clean up when app is closed"""
        try:
            self._replay_stop.set()
            if self.nodes_db_dirty:
                self.save_nodes_db()
            if self.store:
                self.persist_state()
                self.store.close()
            self.mqtt_client.loop_stop()
            if self.connected:
                self.mqtt_client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    app = MeshTextualMonitor()
    app.run()
