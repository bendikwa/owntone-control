import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from control_panel import (
    INPUT_PINS,
    PixelConfig,
    StateService,
    _GPIOInput,
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


class GPIOInputTests(unittest.TestCase):
    def test_low_is_pressed_and_high_is_released(self):
        class FakeGPIO:
            LOW = 0

            def __init__(self):
                self.value = self.LOW

            def input(self, pin):
                return self.value

        gpio = FakeGPIO()
        button = _GPIOInput(gpio, 17)

        self.assertTrue(button.is_pressed)
        gpio.value = 1
        self.assertFalse(button.is_pressed)


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
    def test_switch_inputs_use_rpi_gpio_polling_and_cleanup(self):
        setup_calls = []
        cleanup_calls = []

        class TestStrip(FakeStrip):
            def begin(self):
                pass

        gpio = types.ModuleType("RPi.GPIO")
        gpio.BCM = "BCM"
        gpio.IN = "IN"
        gpio.PUD_UP = "PUD_UP"
        gpio.LOW = 0
        gpio.setmode = lambda mode: setup_calls.append(("mode", mode))
        gpio.setup = lambda pin, mode, pull_up_down: setup_calls.append(
            (pin, mode, pull_up_down)
        )
        gpio.input = lambda pin: gpio.LOW
        gpio.cleanup = lambda pins: cleanup_calls.append(pins)
        rpi = types.ModuleType("RPi")
        rpi.GPIO = gpio
        ws281x = types.ModuleType("rpi_ws281x")
        ws281x.Color = color
        ws281x.PixelStrip = lambda *args, **kwargs: TestStrip()

        with (
            patch.dict(
                sys.modules,
                {
                    "RPi": rpi,
                    "RPi.GPIO": gpio,
                    "rpi_ws281x": ws281x,
                },
            ),
            patch.object(StateService, "run", side_effect=KeyboardInterrupt),
            patch.object(StateService, "turn_off_pixels"),
        ):
            run_service(Path("."))

        self.assertEqual(
            setup_calls,
            [("mode", gpio.BCM)]
            + [(pin, gpio.IN, gpio.PUD_UP) for pin in INPUT_PINS],
        )
        self.assertEqual(cleanup_calls, [INPUT_PINS])


if __name__ == "__main__":
    unittest.main()
