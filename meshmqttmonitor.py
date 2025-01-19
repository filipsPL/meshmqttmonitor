import ssl
import paho.mqtt.client as mqtt
import json
import os
import time
from termcolor import colored
from datetime import datetime
from tabulate import tabulate
import pickle

# Import configuration variables from config.py
from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_TOPIC,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    USE_SSL,
    ALIAS_MAP,
    COLOR_MAP,
    IGNORE_FIELDS,
)


# Try to import optional vatiables; if not defined, set it to None
try:
    from config import CLIENT_ID
except ImportError:
    CLIENT_ID = None


# roles

ROLES = {
    0: "CLIENT",
    1: "CLIENT_MUTE",
    2: "ROUTER",
    3: "ROUTER_CLIENT",
    4: "REPEATER",
    5: "TRACKER",
    6: "SENSOR",
    7: "TAK",
    8: "CLIENT_HIDDEN",
    9: "LOST_AND_FOUND",
    10: "TAK_TRACKER",
}


# taken from Liam https://github.com/liamcottle/meshtastic-map/ - thanks!
HARDWARE = {
    0: "UNSET",
    1: "TLORA_V2",
    2: "TLORA_V1",
    3: "TLORA_V2_1_1P6",
    4: "TBEAM",
    5: "HELTEC_V2_0",
    6: "TBEAM_V0P7",
    7: "T_ECHO",
    8: "TLORA_V1_1P3",
    9: "RAK4631",
    10: "HELTEC_V2_1",
    11: "HELTEC_V1",
    12: "LILYGO_TBEAM_S3_CORE",
    13: "RAK11200",
    14: "NANO_G1",
    15: "TLORA_V2_1_1P8",
    16: "TLORA_T3_S3",
    17: "NANO_G1_EXPLORER",
    18: "NANO_G2_ULTRA",
    19: "LORA_TYPE",
    20: "WIPHONE",
    21: "WIO_WM1110",
    22: "RAK2560",
    23: "HELTEC_HRU_3601",
    25: "STATION_G1",
    26: "RAK11310",
    27: "SENSELORA_RP2040",
    28: "SENSELORA_S3",
    29: "CANARYONE",
    30: "RP2040_LORA",
    31: "STATION_G2",
    32: "LORA_RELAY_V1",
    33: "NRF52840DK",
    34: "PPR",
    35: "GENIEBLOCKS",
    36: "NRF52_UNKNOWN",
    37: "PORTDUINO",
    38: "ANDROID_SIM",
    39: "DIY_V1",
    40: "NRF52840_PCA10059",
    41: "DR_DEV",
    42: "M5STACK",
    43: "HELTEC_V3",
    44: "HELTEC_WSL_V3",
    45: "BETAFPV_2400_TX",
    46: "BETAFPV_900_NANO_TX",
    47: "RPI_PICO",
    48: "HELTEC_WIRELESS_TRACKER",
    49: "HELTEC_WIRELESS_PAPER",
    50: "T_DECK",
    51: "T_WATCH_S3",
    52: "PICOMPUTER_S3",
    53: "HELTEC_HT62",
    54: "EBYTE_ESP32_S3",
    55: "ESP32_S3_PICO",
    56: "CHATTER_2",
    57: "HELTEC_WIRELESS_PAPER_V1_0",
    58: "HELTEC_WIRELESS_TRACKER_V1_0",
    59: "UNPHONE",
    60: "TD_LORAC",
    61: "CDEBYTE_EORA_S3",
    62: "TWC_MESH_V4",
    63: "NRF52_PROMICRO_DIY",
    64: "RADIOMASTER_900_BANDIT_NANO",
    65: "HELTEC_CAPSULE_SENSOR_V3",
    66: "HELTEC_VISION_MASTER_T190",
    67: "HELTEC_VISION_MASTER_E213",
    68: "HELTEC_VISION_MASTER_E290",
    69: "HELTEC_MESH_NODE_T114",
    70: "SENSECAP_INDICATOR",
    71: "TRACKER_T1000_E",
    72: "RAK3172",
    73: "WIO_E5",
    74: "RADIOMASTER_900_BANDIT",
    75: "ME25LS01_4Y10TD",
    76: "RP2040_FEATHER_RFM95",
    77: "M5STACK_COREBASIC",
    78: "M5STACK_CORE2",
    255: "PRIVATE_HW",
}

# nlPoint = chr(10)
nlPoint = "\n • "

# Initialize a dictionary to hold the last message for each type and node
node_data = {}
last_update_time = None
ALIAS_MAP_WITH_NEW = ALIAS_MAP.copy()


def load_nodes_db(file_path="nodes_db.pkl"):
    """
    Loads the `nodes_db` dictionary from a pickle file if it exists.

    Parameters:
        file_path (str): Path to the pickle file.

    Returns:
        dict: The loaded `nodes_db` dictionary or an empty dictionary if the file does not exist or fails to load.
    """
    if os.path.exists(file_path):
        try:
            with open(file_path, "rb") as file:
                nodes_db = pickle.load(file)
                return nodes_db
        except Exception as e:
            print(f"Error loading nodes_db from file: {e}")
    else:
        print(f"{file_path} does not exist. Starting with an empty nodes_db.")

    return {}  # Return an empty dictionary if the file doesn't exist or fails to load


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        display_data()
        print(f"{colored('●', 'green')} Connected to MQTT Broker! Waiting for the first message.")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"{colored('●', 'red')} Failed to connect, return code {rc}")


def on_disconnect(client, userdata, rc):
    print("Disconnected from MQTT Broker. Attempting to reconnect...")
    while True:
        try:
            client.reconnect()
            display_data()
            print(f"{colored('●', 'green')} Reconnected to MQTT Broker!")
            break
        except:
            display_data()
            print(f"{colored('●', 'red')} Reconnection failed. Retrying in 5 seconds...")
            time.sleep(5)


# Function to clear the screen
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def nodeDecToStr(value):
    node_id = f"!{str(hex(value))[2:]}"
    alias = ALIAS_MAP_WITH_NEW.get(node_id, node_id)
    color = COLOR_MAP.get(alias, "white")
    return colored(alias, color)


def int_to_ascii_bar(value, max_value=7, size=7):
    """
    Create an ASCII bar representing the value as a percentage of max_value.

    Parameters:
        value (int): The current value to represent.
        max_value (int): The maximum value for scaling the bar.
        size (int): The total size (length) of the bar.

    Returns:
        str: A string representing the bar with the value and percentage.
    """
    # Ensure value is within bounds
    if value > max_value:
        value = max_value
    elif value < 0:
        value = 0

    # Calculate the percentage and scaled value
    percentage = (value / max_value) * 100 if max_value > 0 else 0
    scaled_value = int((value / max_value) * size) if max_value > 0 else 0

    # Create the bar
    bar = "█" * scaled_value + "░" * (size - scaled_value)

    # Format the output with value, percentage, and the bar
    tekst = f"{value}/{max_value} ({percentage:>5.1f}%) {bar}"
    return tekst


# Function to format the message payload as tab-delimited "variable: value" pairs
def format_message(payload):
    text = ""
    sender_mesh = ""
    recipient_mesh = ""
    sender_mesh_uptime = ""
    for key, value in payload.items():
        if key not in IGNORE_FIELDS:
            if key == "payload":
                try:
                    for key2, value2 in value.items():
                        # Skip keys that are in the IGNORE_FIELDS list
                        if key2 in IGNORE_FIELDS:
                            continue
                        # Add the formatted string to the text with proper indentation and a newline
                        if key2 == "time":
                            received_time_str, circle_color, time_ago_str = format_timestamp(value2)
                            value2 = f"{colored('●', circle_color)} {time_ago_str}"
                        elif key2 in ["node_id", "last_sent_by_id"]:  #
                            value2 = nodeDecToStr(value2)
                        elif key2 == "role":
                            value2 = ROLES[int(value2)]
                        elif key2 == "hardware":
                            value2 = HARDWARE[int(value2)]
                        elif key2 == "battery_level":
                            value2 = int_to_ascii_bar(int(value2), max_value=100, size=5)
                        elif key2 in ["latitude_i", "longitude_i"]:
                            value2 = f"{(int(value2) * 1e-7):.5f}º"
                            key2 = key2[:3]
                        elif key2 in [
                            "channel_utilization",
                            "voltage",
                            "air_util_tx",
                            "temperature",
                            "barometric_pressure",
                            "relative_humidity",
                        ]:
                            # long floats
                            value2 = f"{value2:.3f}"
                        elif key2 in ["uptime_seconds"]:
                            value2 = format_time_human(value2)
                            key2 = "uptime"
                            sender_mesh_uptime = value2

                        text += f"  • {key2}: {value2}\n"
                except:
                    text = "error decoding text"

            elif key == "sender":
                sender_mqtt = ALIAS_MAP_WITH_NEW.get(value, value)
            elif key == "channel":
                channel = value
            elif key == "text":
                # Check if the message is encrypted
                is_encrypted = payload.get("encrypted", False)
                value = "*** ENCRYPTED TEXT ***" if is_encrypted else value

            # formatting of other elements
            elif key in ["last_sent_by_id", "from", "to", "sender"]:
                value = nodeDecToStr(value)
            elif key == "hop_start" or key == "hops_away":
                value = int_to_ascii_bar(value)

            # remove or keep
            if key not in ["from", "to", "payload", "channel", "sender"]:
                text += f"{key}: {str(value)[:120]}\n"

            # extract some data
            if key == 'from':
                sender_mesh = value
            elif key == 'to':
                recipient_mesh = value


    return sender_mqtt, channel, text, sender_mesh, recipient_mesh, sender_mesh_uptime


# Function to calculate the color of the circle based on message age
def get_circle_color(seconds_ago):
    if seconds_ago < 60:  # less than 1 minute ago
        return "green"
    elif seconds_ago < 120:  # 1 to 2 minutes ago
        return "light_green"
    elif seconds_ago < 300:  # 2 to 5 minutes ago
        return "yellow"
    elif seconds_ago < 600:  # 5 to 10 minutes ago
        return "light_yellow"
    elif seconds_ago < 900:  # 10 to 15 minutes ago
        return "magenta"
    elif seconds_ago < 1200:  # 15 to 20 minutes ago
        return "light_magenta"
    elif seconds_ago < 1800:  # 20 to 30 minutes ago
        return "red"
    elif seconds_ago < 3600:  # 30 to 60 minutes ago
        return "light_red"
    else:  # more than 2 hours ago
        return "white"


def format_timestamp(received_timestamp):
    received_time = datetime.fromtimestamp(received_timestamp)
    current_time = datetime.now()
    time_ago = (current_time - received_time).total_seconds()
    circle_color = get_circle_color(time_ago)
    time_ago_str = format_time_human(int(time_ago))
    received_time_str = f"{received_time.strftime('%Y-%m-%d %H:%M:%S')}\n{colored('●', circle_color)} {time_ago_str}"
    return received_time_str, circle_color, time_ago_str


def format_time_human(seconds):
    """
    Format time into seconds, minutes, hours, days, or weeks based on its value.

    Parameters:
        seconds (int): Time in seconds.

    Returns:
        str: A formatted string representing the time in the most appropriate unit.
    """
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
        return f"{weeks:.1f} weeks"


def update_nodes_db(messages, file_path="nodes_db.pkl"):
    """
    Updates the global `nodes_db` dictionary with data from `nodeinfo` messages
    and saves the updated dictionary to a pickle file only if a new record is added.

    Parameters:
        messages (dict): A dictionary of messages for a node.
        file_path (str): Path to the pickle file where `nodes_db` is saved.
    """
    global nodes_db
    is_updated = False  # Track if the dictionary is updated

    # Update nodes_db with new nodeinfo data
    for msg_type, content in messages.items():
        if msg_type == "nodeinfo":
            node_id = content["payload"].get("id", "unknown")
            node_name = content["payload"].get("longname", "Unnamed Node")

            # Check if the node_id is new or has a different name
            if node_id not in nodes_db or nodes_db[node_id] != node_name:
                nodes_db[node_id] = node_name
                is_updated = True  # Mark as updated

    # Save the updated nodes_db to a pickle file only if changes were made
    if is_updated:
        try:
            with open(file_path, "wb") as file:
                pickle.dump(nodes_db, file)
            # print(f"nodes_db successfully updated and saved to {file_path}")
        except Exception as e:
            print(f"Error saving nodes_db to file: {e}")
    # else:
    #     # print("No changes made to nodes_db. Pickle file not saved.")

def format_node_list_and_times(unique_nodes):
    # sort by time ascending
    sorted_nodes_list = dict(sorted(unique_nodes.items(), key=lambda item: item[1], reverse=True))

    # format table
    nodes_list = []
    for sender, timestamp in sorted_nodes_list.items():
        received_time_str, circle_color, time_ago_str = format_timestamp(timestamp)
        color = COLOR_MAP.get(sender, "white")
        nodes_list.append([f"{colored(sender, color)}", f"{colored('●', circle_color)}", f"{time_ago_str}"])


    return tabulate(nodes_list, tablefmt="plain")


def display_data():
    """
    Display MQTT topic, broker details, last update time,
    and a summary of node data in a tabulated format.
    """
    clear_screen()
    global last_update_time, ALIAS_MAP_WITH_NEW, nodes_db

    # Display MQTT topic, broker, and last update time at the top
    last_update_msg = f"Last Update: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n" if last_update_time else "Last Update: N/A\n"

    # Merge `nodes_db` and `ALIAS_MAP` to update aliases
    ALIAS_MAP_WITH_NEW = {**nodes_db, **ALIAS_MAP}

    # Sort node data alphabetically by alias or node_id
    sorted_node_data = sorted(node_data.items(), key=lambda item: ALIAS_MAP_WITH_NEW.get(item[0], item[0]))

    table_data = []
    unique_nodes_mqtt = {}
    unique_nodes_mesh = {}
    unique_nodes_mesh_uptime = {}
    node_details = []

    headers = ["MQTT nodes", "Mesh nodes", "Type, channel", "Last Message", "Received"]

    # Single loop to create summary and detailed table
    for node_id, messages in sorted_node_data:
        recipient_mqtt = ALIAS_MAP_WITH_NEW.get(node_id, node_id)
        color = COLOR_MAP.get(recipient_mqtt, "white")

        # Update `nodes_db` with `nodeinfo` messages
        update_nodes_db(messages)

        # Determine the latest message
        try:
            latest_message_type = max(messages, key=lambda k: messages[k]["timestamp"])
            latest_message = messages[latest_message_type]
            received_timestamp = latest_message.get("timestamp", None)
        except:
            pass

        # for summary line
        if received_timestamp is not None:
            received_time_str, circle_color, time_ago_str = format_timestamp(received_timestamp)
        else:
            circle_color, time_ago_str, received_time_str = (
                "black",
                "unknown",
                "unknown",
            )

        # Add summary line for the node
        # summary_lines_mqtt.append([f"{colored(recipient_mqtt, color)}", f"{colored('●', circle_color)}", f"{time_ago_str}"])
        # if the message is newer than the one in database - replace it
        unique_nodes_mqtt[recipient_mqtt] = min(unique_nodes_mqtt.get(recipient_mqtt, float('inf')), received_timestamp)


        # Build detailed table data
        for msg_type, content in messages.items():
            try:
                received_timestamp2 = content.get("timestamp", None)
                if received_timestamp2 is not None:
                    received_time_str, circle_color, time_ago_str = format_timestamp(received_timestamp2)
                else:
                    circle_color, time_ago_str, received_time_str = ("black", "unk", "unk")

                sender_mqtt, channel, formatted_message, sender_mesh, recipient_mesh, sender_mesh_uptime = format_message(content)

                # if the message is newer than the one in database - replace it
                unique_nodes_mesh[sender_mesh] = min(unique_nodes_mesh.get(sender_mesh, float('inf')), received_timestamp2)
                unique_nodes_mesh_uptime[sender_mesh] = sender_mesh_uptime


                column1 = f"{colored(sender_mqtt, color)}\n    🡇\n{colored(recipient_mqtt, color)}"
                column2 = f"{colored(sender_mesh, color)}\n    🡇\n{colored(recipient_mesh, color)}"
                column3 = f"{msg_type.upper()}\n\n    #{channel}"
                table_data.append([column1, column2, column3, formatted_message, received_time_str])
            except:
                pass

    # Print detailed table
    column1 = tabulate(table_data, headers=headers, tablefmt="simple_grid")

    column2 =  f"MQTT Topic: {MQTT_TOPIC}\nBroker: {MQTT_BROKER}:{MQTT_PORT}\n{last_update_msg}\n"
    column2 += f"{colored('MQTT nodes:', 'white', 'on_dark_grey')}\n" + format_node_list_and_times(unique_nodes_mqtt) + "\n\n"
    column2 += f"{colored('Mesh nodes:', 'white', 'on_dark_grey')}\n" + format_node_list_and_times(unique_nodes_mesh) + "\n\n"
    column2 += f"Node database contains {len(ALIAS_MAP_WITH_NEW)} records."

    print(tabulate([[column1, column2]], tablefmt="plain"))


# Callback when a message is received
def on_message(client, userdata, msg):
    global last_update_time
    try:
        payload = json.loads(msg.payload.decode("ascii", "ignore"))
        msg_type = payload.get("type", "unk")
        node_id = payload.get("sender", "unk")

        if node_id not in node_data:
            node_data[node_id] = {}

        node_data[node_id][msg_type] = payload
        last_update_time = datetime.now()
        # Display the updated data
        display_data()
    except json.JSONDecodeError as e:
        # print(f"Failed to decode JSON message: {str(e)}")
        pass
    except Exception as e:
        # print(f"Error processing message: {str(e)}")
        pass


clear_screen()

nodes_db = load_nodes_db()

# MQTT setup

# Check if CLIENT_ID is defined and set it if available
if "CLIENT_ID":
    client = mqtt.Client(client_id=CLIENT_ID)
else:
    client = mqtt.Client()

client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# Set username and password if necessary
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

if USE_SSL:
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

# Set the reconnection delay
client.reconnect_delay_set(min_delay=1, max_delay=30)

client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.subscribe(MQTT_TOPIC)

# Run the MQTT loop in a separate thread
client.loop_start()

# Keep the script running to listen to MQTT messages
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    client.loop_stop()
    client.disconnect()
    print("MQTT client disconnected.")
