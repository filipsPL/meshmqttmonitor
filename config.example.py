# Define MQTT settings
MQTT_BROKER = "broker.example.org"
MQTT_PORT = 8883
MQTT_TOPIC = "msh/PL/2/json/#"
MQTT_USERNAME = "user"
MQTT_PASSWORD = "changeme"

# Optional client ID
# CLIENT_ID = ''

# SSL Usage
USE_SSL = True

# Alias mapping for node IDs to human-readable names
# Set True only for brokers with self-signed certificates (disables verification)
TLS_INSECURE = False

# Optional: subscribe to several topics instead of MQTT_TOPIC. Binary topics (/e/ and /map/) next to a
# "/json/" topic are added and decoded automatically (set DECODE_PROTOBUF = False to disable).
# MQTT_TOPICS = ["msh/PL/2/json/#", "msh/EU_868/2/json/#"]

# Optional: SQLite log (packets, last seen, telemetry) - used for restart restore and --replay
# DB_PATH = "mesh_log.sqlite"
LOG_RETENTION_DAYS = 14
NODE_RETENTION_DAYS = 7

# Optional: home position (lat, lon) - enables the Dist column and distances in node details
# HOME_POSITION = (52.2297, 21.0122)

# Alerts for these node IDs (default: every node in NODES)
# WATCH_NODES = ["!da5aced8"]
LOW_BATTERY_PERCENT = 20
OFFLINE_MINUTES = 30

# Text messages containing one of these words raise a notification
# MENTION_WORDS = ["filip", "sq5"]

# One line per node:  "node id": ("friendly name", "color")
# Color is a hex value ("#c42cf0") or a color name ("green"); it may be omitted: "node id": "name"
NODES = {
    "!da5aced8": ("my-node-01", "#c42cf0"),
    "!ffffffff": ("everybody", "#dbe3d9"),
    # "!12345678": ("another node", "cyan"),
}

CHART_WIDTH = 200

# Keys for encrypted channels, by channel name (base64). Channels using the default key need nothing.
# CHANNEL_KEYS = {"filips": "base64key=="}

# channel keys:
CHANNEL_KEYS = {
  "PLwaw":   "AA==",
  "WAWtest": "QQ==",
}