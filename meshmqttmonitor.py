import ssl
import paho.mqtt.client as mqtt
import json
import os
import time
from termcolor import colored
from datetime import datetime
from tabulate import tabulate

# Import configuration variables from config.py
from config import MQTT_BROKER, MQTT_PORT, MQTT_TOPIC, MQTT_USERNAME, MQTT_PASSWORD, USE_SSL, ALIAS_MAP, COLOR_MAP, IGNORE_FIELDS

# Initialize a dictionary to hold the last message for each type and node
node_data = {}
last_update_time = None

def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))

# Function to clear the screen
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Function to format the message payload as tab-delimited "variable: value" pairs
def format_message(payload):
    text = ""
    for key, value in payload.items():
        if key not in IGNORE_FIELDS:
            if key == 'payload':
                text += "\n".join([f"  - {key2}: {value2}" for key2, value2 in value.items() if key2 not in IGNORE_FIELDS]) + "\n"
            elif key == 'sender':
                sender = ALIAS_MAP.get(value, value)
            elif key == 'channel':
                channel = value
            else:
                text += f"{key}: {value}\n"
    return sender, channel, text

# Function to calculate the color of the circle based on message age
def get_circle_color(seconds_ago):
    if seconds_ago < 60:  # less than 1 minute ago
        return 'green'
    elif seconds_ago < 120:  # 1 to 2 minutes ago
        return 'light_green'
    elif seconds_ago < 300:  # 2 to 5 minutes ago
        return 'yellow'
    elif seconds_ago < 600:  # 5 to 10 minutes ago
        return 'light_yellow'
    elif seconds_ago < 900:  # 10 to 15 minutes ago
        return 'magenta'
    elif seconds_ago < 1200:  # 15 to 20 minutes ago
        return 'light_magenta'
    elif seconds_ago < 1800:  # 20 to 30 minutes ago
        return 'red'
    elif seconds_ago < 3600:  # 30 to 60 minutes ago
        return 'light_red'
    elif seconds_ago < 7200:  # 1 to 2 hours ago
        return 'purple'
    else:  # more than 2 hours ago
        return 'black'


# Function to display the data in a table format using tabulate
def display_data():
    clear_screen()
    global last_update_time
    current_time = datetime.now()

    # Display MQTT topic, broker, and last update time at the top
    if last_update_time:
        last_update_msg = f"Last Update: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    else:
        last_update_msg = "Last Update: N/A\n"

    print(f"MQTT Topic: {MQTT_TOPIC} | Broker: {MQTT_BROKER}:{MQTT_PORT} | {last_update_msg}")

    # Sort the node_data alphabetically by alias or node_id
    sorted_node_data = sorted(node_data.items(), key=lambda item: ALIAS_MAP.get(item[0], item[0]))

    table_data = []
    summary_line = []
    headers = ["Nodes", "Message Type / Channel", "Last Message", "Received Time"]

    # Single loop to create summary and detailed table
    for node_id, messages in sorted_node_data:
        alias = ALIAS_MAP.get(node_id, node_id)
        color = COLOR_MAP.get(alias, "white")

        # Determine the latest message
        latest_message_type = max(messages, key=lambda k: messages[k]['timestamp'])
        latest_message = messages[latest_message_type]
        received_timestamp = latest_message.get('timestamp', None)

        if received_timestamp is not None:
            received_time = datetime.fromtimestamp(received_timestamp)
            time_ago = (current_time - received_time).total_seconds()
            circle_color = get_circle_color(time_ago)
            time_ago_str = f"{int(time_ago)} seconds ago"
            received_time_str = f"{received_time.strftime('%Y-%m-%d %H:%M:%S')}\n{colored('●', circle_color)} {time_ago_str}"
        else:
            circle_color = 'black'
            time_ago_str = "unknown"
            received_time_str = "unknown"

        # Print summary line
        # summary_line = f"{colored(alias, color)}: Last message {latest_message_type.upper()} | {colored('●', circle_color)} {time_ago_str}"
        summary_line.append(f"{colored(alias, color)}: {colored('●', circle_color)} {time_ago_str}")


        # Build detailed table data
        for msg_type, content in messages.items():
            sender, channel, formatted_message = format_message(content)
            first_column = f"{colored(alias, color)}\n    🡅\n{colored(sender, color)}"
            second_column = f"{msg_type.upper()}\n\n    #{channel}"
            table_data.append([first_column, second_column, formatted_message, received_time_str])

    print("\n".join(summary_line))
    print("\n\n")
    print(tabulate(table_data, headers=headers, tablefmt="simple_grid"))


# Callback when a message is received
def on_message(client, userdata, msg):
    global last_update_time
    try:
        payload = json.loads(msg.payload.decode())
        msg_type = payload.get("type", "unknown")
        node_id = payload.get("sender", "unknown")

        if node_id not in node_data:
            node_data[node_id] = {}

        node_data[node_id][msg_type] = payload
        last_update_time = datetime.now()

        # Display the updated data
        display_data()
    except json.JSONDecodeError as e:
        print(f"Failed to decode JSON message: {str(e)}")
    except Exception as e:
        print(f"Error processing message: {str(e)}")

clear_screen()

# MQTT setup
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# Set username and password if necessary
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

if USE_SSL:
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

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
