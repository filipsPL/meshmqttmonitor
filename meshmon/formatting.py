"""Display helpers shared by the widgets and the app"""

from datetime import datetime
from typing import Any, Dict, Tuple

from rich.markup import escape
from textual.widgets import DataTable

from .constants import HARDWARE, ROLES


def role_name(role: Any) -> str:
    try:
        return ROLES.get(int(role), f"ROLE-{int(role)}")
    except (ValueError, TypeError):
        return str(role)


def hardware_name(hw: Any) -> str:
    try:
        return HARDWARE.get(int(hw), f"HW-{int(hw)}")
    except (ValueError, TypeError):
        return str(hw)


def node_label(node_id: str, alias_map: Dict[str, str]) -> str:
    return escape(str(alias_map.get(node_id, node_id)))


def hops_taken(message: Dict[str, Any]) -> str:
    """Hops the packet travelled before reaching the gateway (hops_away = hop_start - hop_limit)"""
    hop_start, hops_away = message.get("hop_start"), message.get("hops_away")
    if not isinstance(hops_away, int) or not isinstance(hop_start, int) or hop_start <= 0:
        return "[dim]?[/dim]"  # old firmware or field missing
    if hops_away == 0:
        return "[green]direct[/green]"
    color = "yellow" if hops_away <= 2 else "red"
    return f"[{color}]{hops_away}[/{color}]"


def format_snr(snr: Any) -> str:
    if not isinstance(snr, (int, float)):
        return ""
    color = "green" if snr >= 0 else "yellow" if snr >= -10 else "red"
    return f"[{color}]{snr:.1f}[/{color}]"


def format_rssi(rssi: Any) -> str:
    if not isinstance(rssi, (int, float)):
        return ""
    color = "green" if rssi >= -90 else "yellow" if rssi >= -110 else "red"
    return f"[{color}]{rssi:.0f}[/{color}]"


def preserve_table_view(table: DataTable, rebuild) -> None:
    """Run rebuild() (which clears and refills the table) keeping cursor and scroll position"""
    cursor_row = table.cursor_row
    scroll_y = table.scroll_y
    rebuild()
    if table.row_count:
        table.move_cursor(row=min(cursor_row, table.row_count - 1), animate=False)
    table.scroll_to(y=scroll_y, animate=False)


def format_timestamp(timestamp: float) -> Tuple[str, str, str]:
    """Format timestamp and calculate time ago with color"""
    try:
        # Validate timestamp
        if not isinstance(timestamp, (int, float)) or timestamp <= 0:
            return "unknown", "bright_black", "unknown"

        received_time = datetime.fromtimestamp(timestamp)
        current_time = datetime.now()
        time_ago_seconds = max(0.0, (current_time - received_time).total_seconds())

        # Get color based on age
        if time_ago_seconds < 60:  # less than 1 minute
            circle_color = "bright_green"
        elif time_ago_seconds < 120:  # 1-2 minutes
            circle_color = "green"
        elif time_ago_seconds < 300:  # 2-5 minutes
            circle_color = "bright_yellow"
        elif time_ago_seconds < 600:  # 5-10 minutes
            circle_color = "yellow"
        elif time_ago_seconds < 900:  # 10-15 minutes
            circle_color = "magenta"
        elif time_ago_seconds < 1200:  # 15-20 minutes
            circle_color = "bright_magenta"
        elif time_ago_seconds < 1800:  # 20-30 minutes
            circle_color = "red"
        elif time_ago_seconds < 3600:  # 30-60 minutes
            circle_color = "bright_red"
        else:  # more than 60 minutes
            circle_color = "bright_black"

        # Format time ago
        time_ago_str = format_time_human(int(time_ago_seconds))

        # Format received time
        received_time_str = received_time.strftime("%Y-%m-%d %H:%M:%S")

        return received_time_str, circle_color, time_ago_str

    except Exception as e:
        print(f"Error formatting timestamp: {e}")
        return "error", "red", "error"


def format_time_human(seconds: int) -> str:
    """Format time into human-readable form"""
    if seconds < 120:
        return f"{seconds} s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f} min"
    elif seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f} h"
    elif seconds < 604800:
        days = seconds / 86400
        return f"{days:.1f} d"
    else:
        weeks = seconds / 604800
        return f"{weeks:.1f} w"
