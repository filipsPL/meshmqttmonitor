"""Textual widgets and screens"""

from collections import deque
from datetime import datetime
from typing import Any, Dict, List

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import Vertical
from textual.widgets import DataTable, Label, RichLog, Static, Sparkline

from .constants import HARDWARE, ROLES
from .formatting import (
    format_rssi,
    format_snr,
    format_timestamp,
    format_time_human,
    hardware_name,
    hops_taken,
    node_label,
    preserve_table_view,
    role_name,
)
from .meshlogic import BROADCAST, decode_neighborinfo, decode_traceroute, distance_km, format_node_id, osm_link
from .settings import CHART_WIDTH


def format_message_preview(message: Dict[str, Any], alias_map: Dict[str, str]) -> str:
    """Format a preview of the message content focusing on key:value data"""
    try:

        preview_parts = []
        # encrypted = message.get("encrypted", False)

        # Handle payload content
        raw_payload = message.get("payload")
        if isinstance(raw_payload, dict):
            payload = raw_payload
            msg_type = str(message.get("type", "")).lower()

            # Handle text messages with priority and improved visibility
            if msg_type == "text":
                if "text" in payload:
                    try:
                        text_content = str(payload["text"])
                        preview_parts.append(f"[bold yellow]{escape(text_content)}[/bold yellow]")
                    except (ValueError, TypeError):
                        pass

                # encrypted = message.get("encrypted", False)
                # text_content = str(message["text"]) if message["text"] is not None else ""
                # if encrypted:
                #     text_content = "*** ENCRYPTED TEXT ***"

                # text_content = payload["text"]
                # # return f"[bold yellow]MESSAGE:[/bold yellow] {text_content}"
                # preview_parts.append(f"TEXT: {text_content}")

            if msg_type == "traceroute":
                for direction, nodes, snrs in decode_traceroute(message):
                    hops = []
                    for i, node in enumerate(nodes):
                        snr = snrs[i - 1] if i > 0 else None
                        hops.append(node_label(node, alias_map) + (f" ({snr:+.1f} dB)" if snr is not None else ""))
                    preview_parts.append(f"{direction}: " + " → ".join(hops))

            if msg_type == "neighborinfo":
                _node, neighbours = decode_neighborinfo(message)
                listed = [node_label(n, alias_map) + (f" ({snr:+.1f})" if snr is not None else "") for n, snr in neighbours]
                preview_parts.append(f"{len(neighbours)} neighbours: " + ", ".join(listed) if listed else "no neighbours")

            # Process different fields based on message type and content
            if msg_type == "nodeinfo":
                # Add hardware info
                if "hardware" in payload:
                    try:
                        hw_code = int(payload["hardware"])
                        hw_name = HARDWARE.get(hw_code, f"HW-{hw_code}")
                        preview_parts.append(f"HW: {hw_name}")
                    except (ValueError, TypeError):
                        pass

                # Add role info
                if "role" in payload:
                    try:
                        role_code = int(payload["role"])
                        role_name = ROLES.get(role_code, f"ROLE-{role_code}")
                        preview_parts.append(f"ROLE: {role_name}")
                    except (ValueError, TypeError):
                        pass

                # Add node name
                if "longname" in payload:
                    longname = str(payload.get("longname", ""))
                    if longname:
                        preview_parts.append(f"NAME: {escape(longname)}")

                # Add version
                if "firmware_version" in payload:
                    preview_parts.append(f"FW: {escape(str(payload['firmware_version']))}")

            # Add position data
            if "latitude_i" in payload and "longitude_i" in payload:
                try:
                    lat = f"{(int(payload['latitude_i']) * 1e-7):.5f}º"
                    lon = f"{(int(payload['longitude_i']) * 1e-7):.5f}º"
                    preview_parts.append(f"lat: {lat}, lon: {lon}")
                except (ValueError, TypeError):
                    pass

            # Add battery level
            if "battery_level" in payload:
                try:
                    level = int(payload["battery_level"])
                    # Create a simple ASCII bar (simplified from int_to_ascii_bar)
                    bar_len = 5
                    filled = int((level / 100) * bar_len)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    preview_parts.append(f"bat: {level}% {bar}")
                except (ValueError, TypeError):
                    pass

            # Add signal quality fields
            if "rx_snr" in payload:
                preview_parts.append(f"SNR: {escape(str(payload['rx_snr']))}")
            if "rx_rssi" in payload:
                preview_parts.append(f"RSSI: {escape(str(payload['rx_rssi']))}")

            # Add utilization metrics
            if "channel_utilization" in payload:
                try:
                    preview_parts.append(f"chan_util: {float(payload['channel_utilization']):.2f}")
                except (ValueError, TypeError):
                    pass
            if "air_util_tx" in payload:
                try:
                    preview_parts.append(f"air_util: {float(payload['air_util_tx']):.2f}")
                except (ValueError, TypeError):
                    pass

            # Add uptime if available
            if "uptime_seconds" in payload:
                try:
                    uptime = format_time_human(payload["uptime_seconds"])
                    preview_parts.append(f"uptime: {uptime}")
                except Exception:
                    pass

            # Add other interesting fields with proper formatting
            for key in ["voltage", "current", "temperature", "relative_humidity", "barometric_pressure"]:
                if key in payload:
                    try:
                        # Format floats with limited precision
                        if isinstance(payload[key], float):
                            preview_parts.append(f"{key}: {payload[key]:.3f}")
                        else:
                            preview_parts.append(f"{key}: {escape(str(payload[key]))}")
                    except (ValueError, TypeError):
                        pass
        elif raw_payload is not None:
            # payload is not a dict
            preview_parts.append(escape(str(raw_payload)))

        # Combine all parts - don't apply width limit for better visibility
        if preview_parts:
            preview_text = ", ".join(preview_parts)
            return preview_text

        # If we didn't find anything meaningful to display
        return f"MSG TYPE: {escape(str(message.get('type', 'unknown')).upper())}"

    except Exception as e:
        print(f"Error in format_message_preview: {e}")
        return escape("[Error formatting message]")


class MessageRateMonitor(Static):
    """Widget to monitor message rate with visualization that works well for low rates"""

    def __init__(self, chart_width=CHART_WIDTH, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_points = [0.0]  # Start with one zero point
        self.chart_width = chart_width  # Configurable chart width
        self.max_points = chart_width  # Use chart width for max points
        self.last_update = datetime.now()
        self.last_count = 0
        self.total_messages = 0
        self.duplicates = 0

    def compose(self) -> ComposeResult:
        """Create child widgets for the message rate monitor"""
        # Title and statistics
        yield Label("[bold]Message Rate[/bold]")
        yield Static("No data yet", id="rate_stats")

        # Create a container to hold the sparklines with guaranteed height
        container = Static("Loading...", id="chart_container", classes="chart_container")
        yield container

    def on_mount(self):
        """Initialize the display after mounting"""
        self.update_chart_display()

    def update_chart_display(self):
        """Update the visualization with current data"""
        try:
            container = self.query_one("#chart_container")

            # Ensure we have enough content to make the container visible
            # even with low or no message rates
            # display_content = "[bold]Messages Per Second:[/bold]\n"
            display_content = ""

            # Characters for different levels - using a simpler set
            # that's more visible for low rates
            bars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

            # Generate visualization even if no data
            if not self.data_points or all(d == 0 for d in self.data_points):
                display_content += "\n[dim]No message activity detected yet...[/dim]\n"
                display_content += "[dim]"
                display_content += "▁" * self.chart_width  # Flat line with configurable width
                display_content += "[/dim]"
            else:
                # Determine scale for the visualization
                # Force a minimum scale to ensure visibility with low rates
                max_val = max(max(self.data_points), 0.1)  # Force minimum of 0.1

                # Display the data points as a bar chart rather than just a line
                # This works better for low values
                sparkline = ""
                for rate in self.data_points[-self.chart_width :]:  # Last chart_width points
                    # Scale with a minimum value to ensure visibility
                    scaled_val = min(1.0, rate / max_val)

                    # Convert to bar height (0-7)
                    bar_idx = min(7, max(0, int(scaled_val * 8)))

                    # For very low values, ensure at least a small bar
                    if rate > 0 and bar_idx == 0:
                        bar_idx = 1

                    sparkline += bars[bar_idx]

                # Add it to the display with colors
                display_content += "\n[green]" + sparkline + "[/green]\n"

                # Add horizontal scale reference
                # scale_line = f"0{' ' * self.chart_width}{max_val:.1f} msg/s"
                # display_content += f"[dim]{scale_line}[/dim]"

            container.update(display_content)
        except Exception as e:
            print(f"Error updating chart display: {str(e)}")

    def update_with_count(self, current_count, duplicates=0, type_summary=""):
        """Update the monitor with the current total message count"""
        try:
            # Ensure current_count is an integer
            current_count = int(current_count) if isinstance(current_count, (int, float, str)) else 0

            now = datetime.now()

            # Calculate time since last update
            interval = (now - self.last_update).total_seconds()
            if interval < 0.9:  # Update about once per second (tolerate timer jitter)
                return

            # Store total message count
            self.total_messages = current_count
            self.duplicates = duplicates

            # Calculate the rate (messages per second)
            count_diff = max(0, current_count - self.last_count)  # Ensure non-negative
            rate = float(count_diff / interval) if interval > 0 else 0.0

            # Add a new data point
            self.data_points.append(rate)

            # Keep only the last max_points data points
            if len(self.data_points) > self.max_points:
                self.data_points = self.data_points[-self.max_points :]

            # Update tracking variables
            self.last_update = now
            self.last_count = current_count

            # Calculate statistics
            current_rate = self.data_points[-1] if self.data_points else 0.0
            max_rate = max(self.data_points) if self.data_points else 0.0
            avg_rate = sum(self.data_points) / len(self.data_points) if self.data_points else 0.0

            # Update the statistics display
            try:
                stats_display = self.query_one("#rate_stats", Static)
                stats_display.update(
                    f"Current: {current_rate:.1f} msg/s | Max: {max_rate:.1f} msg/s | "
                    f"Avg: {avg_rate:.1f} msg/s | Total: {self.total_messages} | Duplicates: {self.duplicates}"
                    + (f"\nLast 60 s: {type_summary}" if type_summary else "")
                )
            except Exception as e:
                print(f"Error updating stats display: {e}")

            # Update the chart display
            self.update_chart_display()

        except Exception as e:
            print(f"Error in update_with_count: {e}")


class DebugWidget(Static):
    """Widget to display debug messages"""

    MAX_LINES = 500

    def compose(self) -> ComposeResult:
        yield Label("[bold]Debug Messages[/bold]")
        yield RichLog(id="debug_output", max_lines=self.MAX_LINES, markup=True, wrap=True, auto_scroll=True)

    def clear_messages(self):
        """Clear all debug messages"""
        log = self.query_one("#debug_output", RichLog)
        log.clear()
        log.write("[green]Debug messages cleared[/green]")

    def add_message(self, message: str, markup: bool = False):
        """Add a new debug message; text is escaped unless markup=True"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = message if markup else escape(message)
        try:
            self.query_one("#debug_output", RichLog).write(f"{escape(f'[{timestamp}]')} {text}")
        except Exception as e:
            print(f"Error updating debug output: {e}")


class NodeListWidget(Static):
    """Widget to display the list of nodes with their status"""

    def __init__(self, title: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = title
        self.node_data: Dict[str, float] = {}  # node_id -> timestamp

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]{self.title}[/bold]")
        yield DataTable(id=f"table_{self.title.lower().replace(' ', '_')}")

    def on_mount(self) -> None:
        """Set up the table when widget is mounted"""
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Node", "Status", "Last Seen", "Role", "HW", "Dist")

    def update_nodes(
        self, nodes: Dict[str, float], alias_map: Dict[str, str], color_map: Dict[str, str], node_info: Dict[str, Dict[str, Any]] = None, home=None
    ) -> None:
        """Update the nodes list with latest data"""
        self.node_data = nodes
        try:
            table = self.query_one(DataTable)

            def rebuild():
                table.clear()
                # Sort nodes by time (most recent first)
                for node_id, timestamp in sorted(self.node_data.items(), key=lambda x: x[1], reverse=True):
                    try:
                        alias = alias_map.get(node_id, node_id)
                        color = color_map.get(alias, "white")
                        _, circle_color, time_ago = format_timestamp(timestamp)
                        info = (node_info or {}).get(node_id, {})
                        name = f"[{color}]{escape(str(alias))}[/{color}]"
                        if alias == node_id and node_id != BROADCAST:
                            name = f"[dim]{escape(node_id)} (unknown)[/dim]"  # never sent nodeinfo
                        role = role_name(info["role"]) if "role" in info else ""
                        if role in ("ROUTER", "ROUTER_CLIENT", "ROUTER_LATE", "REPEATER"):
                            role = f"[cyan]{role}[/cyan]"
                        dist = ""
                        if home and "lat" in info and "lon" in info:
                            dist = f"{distance_km(home[0], home[1], info['lat'], info['lon']):.1f} km"
                        table.add_row(
                            name,
                            f"[{circle_color}]●[/{circle_color}]",
                            time_ago,
                            role,
                            hardware_name(info["hardware"]) if "hardware" in info else "",
                            dist,
                            key=node_id,
                        )
                    except Exception as row_err:
                        print(f"Error adding node row for {node_id}: {row_err}")

            preserve_table_view(table, rebuild)
        except Exception as e:
            print(f"Error updating node list widget: {str(e)}")


class MessagesWidget(Static):
    """Widget to display the list of messages"""

    # Define colors for different message types
    MESSAGE_TYPE_COLORS = {
        "position": "green",
        "nodeinfo": "cyan",
        "text": "yellow",
        "neighborinfo": "magenta",
        "traceroute": "blue",
        "telemetry": "bright_blue",
        "command": "orange",
        "routing": "purple",
        # Add more message types and colors as needed
    }

    def compose(self) -> ComposeResult:
        yield Label("[bold]Messages[/bold]", id="messages_title")
        yield DataTable(id="messages_table")

    def set_title(self, text: str) -> None:
        self.query_one("#messages_title", Label).update(f"[bold]Messages[/bold] {text}")

    def on_mount(self) -> None:
        table = self.query_one("#messages_table", DataTable)
        table.add_columns("From", "To", "Type", "Ch", "Hops", "SNR", "RSSI", "Via", "Content", "Time", "Age")

    alias_map: Dict[str, str] = {}

    def update_messages(self, messages: List[Dict[str, Any]], alias_map: Dict[str, str], color_map: Dict[str, str]) -> None:
        """Update the messages table with latest data"""
        try:
            table = self.query_one("#messages_table", DataTable)

            # Most recent first, limited to 100 rows
            sorted_messages = sorted(messages, key=lambda x: x.get("timestamp", 0), reverse=True)[:100]
            self.alias_map = alias_map

            def rebuild():
                table.clear()
                for i, message in enumerate(sorted_messages):
                    try:
                        self._add_message_row(table, i, message, alias_map, color_map)
                    except Exception as msg_err:
                        print(f"Error adding message row {i}: {str(msg_err)}")

            preserve_table_view(table, rebuild)
        except Exception as e:
            print(f"Error updating messages table: {str(e)}")

    def _add_message_row(self, table: DataTable, i: int, message: Dict[str, Any], alias_map, color_map) -> None:
        # "from" is the originating node; "sender" is the gateway that uplinked it
        sender = message.get("from", message.get("sender", "unknown sender"))
        recipient = message.get("to", "unknown recipient")
        msg_type = str(message.get("type", "unknown msg type")).lower()
        channel = str(message.get("channel", ""))

        sender_alias = alias_map.get(format_node_id(sender), format_node_id(sender))
        recipient_alias = alias_map.get(format_node_id(recipient), format_node_id(recipient))
        sender_color = color_map.get(sender_alias, "white")
        recipient_color = color_map.get(recipient_alias, "white")
        type_color = self.MESSAGE_TYPE_COLORS.get(msg_type, "white")

        try:
            content_preview = self.format_message_preview(message)
        except Exception as preview_err:
            content_preview = escape(f"[Error displaying content: {preview_err}]")

        timestamp = message.get("timestamp", 0)
        received_time_str, circle_color, time_ago = format_timestamp(timestamp)

        gateways = message.get("gateways") or [message.get("sender")]
        gw_ids = [format_node_id(g) for g in gateways if g]
        via = ""
        if gw_ids:
            via = escape(str(alias_map.get(gw_ids[0], gw_ids[0])))
            if len(gw_ids) > 1:
                via += f" [bold]+{len(gw_ids) - 1}[/bold]"

        row_key = f"msg-{i}-{sender}-{recipient}-{int(timestamp) if isinstance(timestamp, (int, float)) else 0}"
        table.add_row(
            f"[{sender_color}]{escape(str(sender_alias))}[/{sender_color}]",
            f"[{recipient_color}]{escape(str(recipient_alias))}[/{recipient_color}]",
            f"[{type_color}]{escape(msg_type.upper())}[/{type_color}]",
            f"#{escape(channel)}" if channel else "",
            hops_taken(message),
            format_snr(message.get("snr")),
            format_rssi(message.get("rssi")),
            via,
            content_preview,
            received_time_str,
            f"[{circle_color}]●[/{circle_color}] {time_ago}",
            key=row_key,
        )

    def format_message_preview(self, message: Dict[str, Any]) -> str:
        return format_message_preview(message, self.alias_map)


class LinksScreen(ModalScreen):
    """Radio links (who hears whom) learned from neighbourinfo and traceroute packets"""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("n", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Label("[bold]Radio links[/bold] (esc / n to close)")
        yield DataTable(id="links_table")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Heard", "By", "SNR", "Age", "Source")
        self.refresh_links()
        self.set_interval(1, self.refresh_links)

    def refresh_links(self) -> None:
        table = self.query_one(DataTable)
        alias_map = self.app.alias_map_with_new

        def rebuild():
            table.clear()
            for (tx, rx), link in sorted(self.app.links.items(), key=lambda kv: kv[1]["timestamp"], reverse=True):
                _, circle_color, time_ago = format_timestamp(link["timestamp"])
                table.add_row(
                    node_label(tx, alias_map),
                    node_label(rx, alias_map),
                    format_snr(link["snr"]),
                    f"[{circle_color}]●[/{circle_color}] {time_ago}",
                    link["source"],
                )

        preserve_table_view(table, rebuild)


class NodeDetailScreen(ModalScreen):
    """Everything known about one node: info, position, telemetry history, links and recent messages"""

    BINDINGS = [Binding("escape", "dismiss", "Close")]
    METRICS = (("battery", "Battery %"), ("voltage", "Voltage V"), ("chan_util", "Channel util %"), ("air_util", "Air util TX %"))

    def __init__(self, node_id: str):
        super().__init__()
        self.node_id = node_id

    def compose(self) -> ComposeResult:
        with Vertical(id="detail_box"):
            yield Static(id="detail_info")
            for key, label in self.METRICS:
                yield Label(label, id=f"lbl_{key}", classes="metric_label")
                yield Sparkline([], id=f"spark_{key}")
            yield Label("[bold]Links[/bold]")
            yield Static(id="detail_links")
            yield Label("[bold]Recent messages[/bold] (esc to close)")
            yield DataTable(id="detail_messages")

    def on_mount(self) -> None:
        self.query_one("#detail_messages", DataTable).add_columns("Type", "Time", "Age", "Content")
        self.refresh_detail()
        self.set_interval(1, self.refresh_detail)

    def refresh_detail(self) -> None:
        app = self.app
        alias_map = app.alias_map_with_new
        node = self.node_id
        info = app.node_info.get(node, {})

        lines = [f"[bold]{node_label(node, alias_map)}[/bold]  {escape(node)}"]
        details = []
        if "role" in info:
            details.append(f"role {role_name(info['role'])}")
        if "hardware" in info:
            details.append(f"hw {hardware_name(info['hardware'])}")
        if details:
            lines.append(", ".join(details))
        for label, nodes in (("Last seen (mesh)", app.mesh_nodes), ("Last seen (gateway)", app.mqtt_nodes)):
            if node in nodes:
                when, _color, ago = format_timestamp(nodes[node])
                lines.append(f"{label}: {when} ({ago} ago)")
        if "lat" in info and "lon" in info:
            pos = f"Position: {info['lat']:.5f}, {info['lon']:.5f}"
            if "alt" in info:
                pos += f", {info['alt']:.0f} m"
            if app.home:
                pos += f"  ({distance_km(app.home[0], app.home[1], info['lat'], info['lon']):.1f} km from home)"
            lines.append(pos)
            lines.append(f"[dim]{escape(osm_link(info['lat'], info['lon']))}[/dim]")
        self.query_one("#detail_info", Static).update("\n".join(lines))

        history = list(app.telemetry.get(node, ()))
        for key, label in self.METRICS:
            values = [row[key] for row in history if row.get(key) is not None]
            self.query_one(f"#spark_{key}", Sparkline).data = values or [0]
            last = f"{values[-1]:.2f}" if values else "-"
            self.query_one(f"#lbl_{key}", Label).update(f"{label}: {last}  ({len(values)} samples)")

        heard_by = [(tx, rx, l) for (tx, rx), l in app.links.items() if node in (tx, rx)]
        link_lines = []
        for tx, rx, link in sorted(heard_by, key=lambda x: x[2]["timestamp"], reverse=True)[:8]:
            snr = f" {link['snr']:+.1f} dB" if link["snr"] is not None else ""
            link_lines.append(f"{node_label(tx, alias_map)} → {node_label(rx, alias_map)}{snr}")
        self.query_one("#detail_links", Static).update("\n".join(link_lines) or "[dim]none known[/dim]")

        table = self.query_one("#detail_messages", DataTable)
        mine = [m for m in app.all_messages if format_node_id(m.get("from", m.get("sender"))) == node][-10:]

        def rebuild():
            table.clear()
            for m in reversed(mine):
                when, color, ago = format_timestamp(m.get("timestamp", 0))
                preview = format_message_preview(m, alias_map)
                table.add_row(escape(str(m.get("type", "")).upper()), when, f"[{color}]●[/{color}] {ago}", preview)

        preserve_table_view(table, rebuild)


class ChatScreen(ModalScreen):
    """Text messages only, newest at the bottom"""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("h", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Label("[bold]Chat[/bold] (esc / h to close)")
        yield RichLog(id="chat_log", markup=True, wrap=True)

    def on_mount(self) -> None:
        self._shown = 0
        self.refresh_chat()
        self.set_interval(1, self.refresh_chat)

    def refresh_chat(self) -> None:
        app = self.app
        chat = list(app.chat)
        new = chat[max(0, len(chat) - (app.chat_total - self._shown)) :] if app.chat_total > self._shown else []
        log = self.query_one(RichLog)
        for entry in new:
            when = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
            name = node_label(entry["origin"], app.alias_map_with_new)
            color = app.color_for(entry["origin"])
            log.write(f"[dim]{when}[/dim] [dim]#{entry['channel']}[/dim] [{color}]{name}[/{color}]: {escape(entry['text'])}")
        self._shown = app.chat_total
