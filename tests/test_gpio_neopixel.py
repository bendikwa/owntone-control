import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from gpio_neopixel import (
    INPUT_PINS,
    PixelConfig,
    StateService,
    parse_pixel_config,
    run_service,
    scaled_color,
)


class FakeButton:
    def __init__(self, is_pressed):
        self.is_pressed = is_pressed


class FakeStrip:
    def __init__(self):
        self.colors = {}
        self.show_count = 0

    def setPixelColor(self, index, color):
        self.colors[index] = color

    def show(self):
        self.show_count += 1


def color(red, green, blue):
    return red, green, blue


class PixelConfigTests(unittest.TestCase):
    def test_parses_config_and_scales_brightness(self):
        config = parse_pixel_config(
            '{"on": true, "color": "#33AAFF", "brightness": 0.4}'
        )
        self.assertEqual(config, PixelConfig(True, (51, 170, 255), 0.4))
        self.assertEqual(scaled_color(config), (20, 68, 102))

    def test_off_config_is_black(self):
        config = PixelConfig(False, (255, 10, 20), 1.0)
        self.assertEqual(scaled_color(config), (0, 0, 0))

    def test_rejects_invalid_values(self):
        invalid = (
            "[]",
            '{"on": 1, "color": "#000000", "brightness": 1}',
            '{"on": true, "color": "red", "brightness": 1}',
            '{"on": true, "color": "#000000", "brightness": 1.1}',
            '{"on": true, "color": "#000000", "brightness": true}',
        )
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_pixel_config(content)


class StateServiceTests(unittest.TestCase):
    def test_switch_files_follow_active_low_button_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            buttons = [FakeButton(True), FakeButton(False), FakeButton(True), FakeButton(False)]
            service = StateService(state_dir, buttons, FakeStrip(), color)

            service.sync_switch_files()

            self.assertTrue((state_dir / "1.in").exists())
            self.assertFalse((state_dir / "2.in").exists())
            self.assertTrue((state_dir / "3.in").exists())
            self.assertFalse((state_dir / "4.in").exists())

            buttons[0].is_pressed = False
            service.sync_switch_files()
            self.assertFalse((state_dir / "1.in").exists())

    def test_missing_output_file_turns_pixel_off(self):
        with tempfile.TemporaryDirectory() as directory:
            strip = FakeStrip()
            service = StateService(Path(directory), [FakeButton(False)] * 4, strip, color)

            service.sync_pixel_files()

            self.assertEqual(strip.colors, {index: (0, 0, 0) for index in range(5)})

    def test_output_file_sets_matching_pixel(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "3.out").write_text(
                '{"on": true, "color": "#FF8000", "brightness": 0.5}',
                encoding="utf-8",
            )
            strip = FakeStrip()
            service = StateService(state_dir, [FakeButton(False)] * 4, strip, color)

            service.sync_pixel_files()

            self.assertEqual(strip.colors[3], (128, 64, 0))
            self.assertEqual(strip.colors[0], (0, 0, 0))


class RunServiceTests(unittest.TestCase):
    def test_buttons_use_lgpio_factory(self):
        created_buttons = []
        created_factories = []

        class FakeFactory:
            def __init__(self):
                self.closed = False
                created_factories.append(self)

            def close(self):
                self.closed = True

        class FakeButton:
            def __init__(self, pin, pull_up, pin_factory):
                created_buttons.append((pin, pull_up, pin_factory))

            def close(self):
                pass

        class TestStrip(FakeStrip):
            def begin(self):
                pass

        gpiozero = types.ModuleType("gpiozero")
        gpiozero.Button = FakeButton
        gpiozero_lgpio = types.ModuleType("gpiozero.pins.lgpio")
        gpiozero_lgpio.LGPIOFactory = FakeFactory
        ws281x = types.ModuleType("rpi_ws281x")
        ws281x.Color = color
        ws281x.PixelStrip = lambda *args, **kwargs: TestStrip()

        with (
            patch.dict(
                sys.modules,
                {
                    "gpiozero": gpiozero,
                    "gpiozero.pins.lgpio": gpiozero_lgpio,
                    "rpi_ws281x": ws281x,
                },
            ),
            patch.object(StateService, "run", side_effect=KeyboardInterrupt),
            patch.object(StateService, "turn_off_pixels"),
        ):
            run_service(Path("."))

        self.assertEqual([pin for pin, _, _ in created_buttons], list(INPUT_PINS))
        self.assertTrue(all(pull_up for _, pull_up, _ in created_buttons))
        self.assertTrue(
            all(factory is created_factories[0] for _, _, factory in created_buttons)
        )
        self.assertTrue(created_factories[0].closed)


if __name__ == "__main__":
    unittest.main()
