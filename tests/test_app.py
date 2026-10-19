import asyncio
import json
import threading
import time

from textual.widgets import DataTable

import meshmqttmonitor2 as m


class FakeMsg:
    topic = "msh/test"

    def __init__(self, data):
        self.payload = json.dumps(data).encode()


BASE = {"from": 0x0ABC1234, "id": 77, "to": 4294967295, "type": "text", "channel": 0, "payload": {"text": "hi [/foo] [bold]"}, "hop_start": 7, "hops_away": 2}
MESSAGES = [
    dict(BASE, sender="!da5aced8", snr=-3.0, rssi=-100),
    dict(BASE, sender="!b03bbe9c", snr=6.5, rssi=-80),  # duplicate via another gateway
    {"sender": "!b03bbe9c", "from": 0xB03BBE9C, "id": 2, "type": "telemetry", "payload": {"battery_level": 5, "voltage": 3.3}},
    {"sender": "!b03bbe9c", "from": 0xB03BBE9C, "id": 3, "type": "position", "payload": {"latitude_i": 521000000, "longitude_i": 210000000}},
    {"sender": "!b03bbe9c", "from": 0xB03BBE9C, "id": 4, "type": "nodeinfo", "payload": {"id": "!b03bbe9c", "longname": "N", "role": 2, "hardware": 43}},
]


def make_app(tmp_path):
    m.DB_PATH = str(tmp_path / "log.sqlite")
    m.NODES_DB_PATH = str(tmp_path / "nodes.pkl")
    m.WATCH_NODES = ["!b03bbe9c"]
    m.BASE_DIR = str(tmp_path)
    m.LOG_ENABLED = True
    app = m.MeshTextualMonitor()
    app.mqtt_client.connect_async = lambda *a: None
    app.mqtt_client.loop_start = lambda *a: None
    return app


def feed(app):
    def send():
        for d in MESSAGES:
            app.on_mqtt_message(None, None, FakeMsg(d))

    t = threading.Thread(target=send)
    t.start()
    t.join()


def test_pipeline_and_screens(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(200, 60)) as pilot:
            feed(app)
            await pilot.pause(1.5)
            table = app.query_one("#messages_table", DataTable)
            assert table.row_count == 4  # duplicate merged
            assert app.duplicate_count == 1 and app.total_message_count == 4
            assert app.node_info["!b03bbe9c"]["role"] == 2
            assert app.node_info["!b03bbe9c"]["lat"] > 52
            assert list(app.telemetry["!b03bbe9c"])[0]["battery"] == 5
            assert "!b03bbe9c" in app.low_battery_alerted
            assert app.chat_total == 1

            app.search_text = "telemetry"
            app.update_messages_widget()
            assert table.row_count == 1
            await pilot.press("x")
            assert table.row_count == 4
            await pilot.press("t")
            assert app.type_filter != ""
            await pilot.press("x")

            await pilot.press("p")
            assert app.paused
            await pilot.press("p")

            for key, screen in (("n", "LinksScreen"), ("h", "ChatScreen")):
                await pilot.press(key)
                await pilot.pause(1.2)
                assert type(app.screen).__name__ == screen
                await pilot.press("escape")

            from meshmon.widgets import NodeDetailScreen

            app.push_screen(NodeDetailScreen("!b03bbe9c"))
            await pilot.pause(1.2)
            assert app.screen.query_one("#detail_messages").row_count >= 1
            await pilot.press("escape")

            app.action_export_gpx()
        assert (tmp_path / "log.sqlite").exists()

    asyncio.run(run())


def test_restore_after_restart(tmp_path):
    async def first():
        app = make_app(tmp_path)
        async with app.run_test(size=(200, 60)) as pilot:
            feed(app)
            await pilot.pause(1.5)

    asyncio.run(first())

    async def second():
        app = make_app(tmp_path)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.pause(1.2)
            assert app.query_one("#messages_table", DataTable).row_count == 4
            assert app.node_info["!b03bbe9c"]["role"] == 2
            assert "!b03bbe9c" in app.stored_mesh_nodes
            assert len(app.telemetry["!b03bbe9c"]) == 1

    asyncio.run(second())


def test_replay(tmp_path):
    async def record():
        app = make_app(tmp_path)
        async with app.run_test(size=(200, 60)) as pilot:
            feed(app)
            await pilot.pause(1.5)

    asyncio.run(record())

    async def replay():
        m.ARGS.replay = str(tmp_path / "log.sqlite")
        m.ARGS.speed = 1000
        m.DB_PATH = str(tmp_path / "other.sqlite")
        m.LOG_ENABLED = False
        try:
            app = m.MeshTextualMonitor()
            async with app.run_test(size=(200, 60)) as pilot:
                await pilot.pause(2.0)
                assert app.query_one("#messages_table", DataTable).row_count == 4
        finally:
            m.ARGS.replay = None

    asyncio.run(replay())


def test_protobuf_topic_reaches_tables(tmp_path):
    from test_protodecode import PORT, envelope

    class BinMsg:
        topic = "msh/PL/2/e/MediumFast/!970f5908"
        payload = envelope(PORT.TEXT_MESSAGE_APP, b"hello from protobuf")

    class Garbage:
        topic = "msh/PL/2/e/MediumFast/!970f5908"
        payload = b"teet"

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(200, 60)) as pilot:
            app.on_mqtt_message(None, None, Garbage())  # must not raise or add rows
            app.on_mqtt_message(None, None, BinMsg())
            await pilot.pause(1.5)
            assert app.query_one("#messages_table", DataTable).row_count == 1
            assert app.chat[0]["text"] == "hello from protobuf"

    asyncio.run(run())
