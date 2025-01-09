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


def int_to_ascii_bar(value, max_value=7):
    if value > max_value:
        value = max_value

    tekst = str(value) + " " + "█" * value + "░" * (max_value - value)
    return tekst


# Function to format the message payload as tab-delimited "variable: value" pairs
def format_message(payload):
    text = ""
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

                        text += f"  - {key2}: {value2}\n"
                except:
                    text = "error decoding text"

            elif key == "sender":
                sender = ALIAS_MAP_WITH_NEW.get(value, value)
            elif key == "channel":
                channel = value
            else:
                # formatting of other elements
                if key in ["from", "to", "last_sent_by_id"]:
                    value = nodeDecToStr(value)
                elif key == "hop_start" or key == "hops_away":
                    value = int_to_ascii_bar(value)
                text += f"{key}: {value}\n"
    return sender, channel, text


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
    time_ago_str = f"{int(time_ago)} seconds ago"
    received_time_str = f"{received_time.strftime('%Y-%m-%d %H:%M:%S')}\n{colored('●', circle_color)} {time_ago_str}"
    return received_time_str, circle_color, time_ago_str


import pickle


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
    summary_lines = []
    headers = ["Nodes", "Message Type / Channel", "Last Message", "Received Time"]

    # Single loop to create summary and detailed table
    for node_id, messages in sorted_node_data:
        alias = ALIAS_MAP_WITH_NEW.get(node_id, node_id)
        color = COLOR_MAP.get(alias, "white")

        # Update `nodes_db` with `nodeinfo` messages
        update_nodes_db(messages)

        # Determine the latest message
        try:
            latest_message_type = max(messages, key=lambda k: messages[k]["timestamp"])
            latest_message = messages[latest_message_type]
            received_timestamp = latest_message.get("timestamp", None)
        except:
            pass

        if received_timestamp is not None:
            received_time_str, circle_color, time_ago_str = format_timestamp(received_timestamp)
        else:
            circle_color, time_ago_str, received_time_str = (
                "black",
                "unknown",
                "unknown",
            )

        # Add summary line for the node
        summary_lines.append(f"{colored(alias, color)}: {colored('●', circle_color)} {time_ago_str}")

        # Build detailed table data
        for msg_type, content in messages.items():
            try:
                sender, channel, formatted_message = format_message(content)
                first_column = f"{colored(alias, color)}\n    🡅\n{colored(sender, color)}"
                second_column = f"{msg_type.upper()}\n\n    #{channel}"
                table_data.append([first_column, second_column, formatted_message, received_time_str])
            except:
                pass

    # Print detailed table
    # print(tabulate(table_data, headers=headers, tablefmt="simple_grid"))
    column1 = tabulate(table_data, headers=headers, tablefmt="simple_grid")

    column2 = f"MQTT Topic: {MQTT_TOPIC} | Broker: {MQTT_BROKER}:{MQTT_PORT} | {last_update_msg}\n\n{chr(10).join(summary_lines)}\nNode database contains {len(ALIAS_MAP_WITH_NEW)} records."

    print(tabulate([[column1, column2]], tablefmt="plain"))


# Callback when a message is received
def on_message(client, userdata, msg):
    global last_update_time
    try:
        payload = json.loads(msg.payload.decode("ascii", "ignore"))
        msg_type = payload.get("type", "unknown")
        node_id = payload.get("sender", "unknown")

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
