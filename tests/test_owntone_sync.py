import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from owntone_sync import (
    AVAILABLE_COLOR,
    BRIGHTNESS,
    CONNECTED_COLOR,
    OwntoneApi,
    OwntoneSync,
    SyncConfig,
    load_config,
)


class FakeApi:
    def __init__(self, outputs):
        self.outputs = outputs
        self.selected = []

    def get_outputs(self):
        return self.outputs

    def select_output(self, output_id):
        self.selected.append(output_id)


class ConfigTests(unittest.TestCase):
    def test_loads_sources_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"sources":["Living Room"]}', encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config.api_url, "http://127.0.0.1:3689")
        self.assertEqual(config.sources, ("Living Room",))
        self.assertEqual(config.poll_interval, 2.0)

    def test_rejects_more_than_four_sources_and_duplicates(self):
        for sources in (
            ["A", "B", "C", "D", "E"],
            ["A", "A"],
        ):
            with self.subTest(sources=sources), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


class ApiTests(unittest.TestCase):
    def test_select_output_uses_encoded_id_and_json_body(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return b""

        with patch("owntone_sync.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            OwntoneApi("http://127.0.0.1:3689").select_output("speaker/1")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "http://127.0.0.1:3689/api/outputs/speaker%2F1"
        )
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(json.loads(request.data), {"selected": True})


class SyncTests(unittest.TestCase):
    def test_switch_connects_available_source_and_leds_show_each_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "1.in").touch()
            (state_dir / "2.in").touch()
            config = SyncConfig(
                "http://127.0.0.1:3689",
                state_dir,
                2.0,
                ("Living Room", "Kitchen", "Missing"),
            )
            api = FakeApi(
                [
                    {
                        "id": "speaker/1",
                        "name": "Living Room",
                        "type": "AirPlay",
                        "selected": False,
                    },
                    {
                        "id": "speaker-2",
                        "name": "Kitchen",
                        "type": "AirPlay",
                        "selected": False,
                    },
                    {
                        "id": "local",
                        "name": "Missing",
                        "type": "ALSA",
                        "selected": True,
                    },
                ]
            )

            OwntoneSync(config, api).sync_once()

            self.assertEqual(api.selected, ["speaker/1", "speaker-2"])
            self.assertEqual(
                json.loads((state_dir / "1.out").read_text(encoding="utf-8")),
                {"on": True, "color": CONNECTED_COLOR, "brightness": BRIGHTNESS},
            )
            self.assertEqual(
                json.loads((state_dir / "2.out").read_text(encoding="utf-8")),
                {"on": True, "color": CONNECTED_COLOR, "brightness": BRIGHTNESS},
            )
            self.assertEqual(
                json.loads((state_dir / "3.out").read_text(encoding="utf-8")),
                {"on": False, "color": AVAILABLE_COLOR, "brightness": BRIGHTNESS},
            )

    def test_selected_source_is_green_and_unselected_source_is_orange(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config = SyncConfig(
                "http://127.0.0.1:3689", state_dir, 2.0, ("Connected", "Available")
            )
            api = FakeApi(
                [
                    {
                        "id": "1",
                        "name": "Connected",
                        "type": "AirPlay",
                        "selected": True,
                    },
                    {
                        "id": "2",
                        "name": "Available",
                        "type": "AirPlay",
                        "selected": False,
                    },
                ]
            )

            OwntoneSync(config, api).sync_once()

            connected = json.loads((state_dir / "1.out").read_text(encoding="utf-8"))
            available = json.loads((state_dir / "2.out").read_text(encoding="utf-8"))
            self.assertEqual(connected["color"], CONNECTED_COLOR)
            self.assertEqual(available["color"], AVAILABLE_COLOR)
            self.assertEqual(api.selected, [])

    def test_failed_connection_keeps_available_color(self):
        class FailingApi(FakeApi):
            def select_output(self, output_id):
                raise OSError("Owntone is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "1.in").touch()
            config = SyncConfig(
                "http://127.0.0.1:3689", state_dir, 2.0, ("Speaker",)
            )
            api = FailingApi(
                [
                    {
                        "id": "1",
                        "name": "Speaker",
                        "type": "AirPlay",
                        "selected": False,
                    }
                ]
            )

            OwntoneSync(config, api).sync_once()

            led = json.loads((state_dir / "1.out").read_text(encoding="utf-8"))
            self.assertEqual(led["color"], AVAILABLE_COLOR)

    def test_leds_without_configured_speakers_are_off(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config = SyncConfig(
                "http://127.0.0.1:3689", state_dir, 2.0, ("Configured",)
            )
            api = FakeApi([])

            OwntoneSync(config, api).sync_once()

            for index in range(1, 5):
                led = json.loads(
                    (state_dir / f"{index}.out").read_text(encoding="utf-8")
                )
                self.assertFalse(led["on"])


if __name__ == "__main__":
    unittest.main()
