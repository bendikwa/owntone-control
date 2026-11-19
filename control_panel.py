#!/usr/bin/env python3
"""Mirror four active-low switches to files and control a five-pixel LED chain."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Sequence


INPUT_PINS = (17, 27, 22, 23)
PIXEL_COUNT = 5
POLL_INTERVAL = 1
SYSTEM_PIXEL_BRIGHTNESS = 0.1
SYSTEM_PIXEL_ORANGE = "#FFA500"
SYSTEM_PIXEL_BLUE = "#000080"
SYSTEM_PIXEL_RED = "#800000"
COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\Z")
LOGGER = logging.getLogger("control_panel")


@dataclass(frozen=True)
class PixelConfig:
    on: bool
    color: tuple[int, int, int]
    brightness: float


def parse_pixel_config(content: str) -> PixelConfig:
    """Parse and validate one pixel's JSON configuration."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("configuration must be a JSON object")
    if set(data) != {"on", "color", "brightness"}:
        raise ValueError("configuration must contain on, color, and brightness only")
    if not isinstance(data["on"], bool):
        raise ValueError("'on' must be a boolean")

    color = data["color"]
    if not isinstance(color, str) or COLOR_PATTERN.fullmatch(color) is None:
        raise ValueError("'color' must be an RGB hex string such as '#33AAFF'")

    brightness = data["brightness"]
    if (
        isinstance(brightness, bool)
        or not isinstance(brightness, (int, float))
        or not math.isfinite(brightness)
        or not 0 <= brightness <= 1
    ):
        raise ValueError("'brightness' must be a finite number from 0.0 to 1.0")

    rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    return PixelConfig(data["on"], rgb, float(brightness))


def scaled_color(config: PixelConfig) -> tuple[int, int, int]:
    if not config.on:
        return (0, 0, 0)
    return tuple(round(channel * config.brightness) for channel in config.color)


def service_is_running(service_name: str) -> bool | None:
    try:
        result = subprocess.run(
            ("systemctl", "is-active", "--quiet", service_name),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        LOGGER.error("Timed out checking %s status", service_name)
        return None
    except OSError as error:
        LOGGER.error("Cannot check %s status: %s", service_name, error)
        return None

    if result.returncode in (0, 3, 4):
        return result.returncode == 0
    LOGGER.error(
        "Unexpected exit status %s while checking %s",
        result.returncode,
        service_name,
    )
    return None


def owntone_service_is_running() -> bool | None:
    return service_is_running("owntone.service")


def owntone_sync_service_is_running() -> bool | None:
    return service_is_running("owntone-sync.service")


def wifi_is_connected(net_dir: Path = Path("/sys/class/net")) -> bool | None:
    try:
        wireless_interfaces = [
            interface
            for interface in net_dir.iterdir()
            if (interface / "wireless").is_dir()
        ]
    except OSError as error:
        LOGGER.error("Cannot inspect network interfaces for Wi-Fi status: %s", error)
        return None

    for interface in wireless_interfaces:
        try:
            carrier = (interface / "carrier").read_text(encoding="ascii").strip()
        except OSError as error:
            LOGGER.error("Cannot read Wi-Fi status for %s: %s", interface.name, error)
            return None
        if carrier == "1":
            return True
        if carrier != "0":
            LOGGER.error(
                "Unexpected Wi-Fi carrier status %r for %s", carrier, interface.name
            )
            return None
    return False


class StateService:
    def __init__(
        self,
        state_dir: Path,
        buttons: Sequence[object],
        strip: object,
        color_factory: object,
    ) -> None:
        if len(buttons) != len(INPUT_PINS):
            raise ValueError(f"expected {len(INPUT_PINS)} switch buttons")
        self.state_dir = state_dir
        self.buttons = buttons
        self.strip = strip
        self.color_factory = color_factory
        self._output_signatures: list[object] = [_UNREAD] * PIXEL_COUNT

    def sync_switch_files(self) -> None:
        for index, button in enumerate(self.buttons, start=1):
            path = self.state_dir / f"{index}.in"
            if button.is_pressed:
                try:
                    path.touch(exist_ok=False)
                except FileExistsError:
                    pass
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def sync_pixel_files(self) -> None:
        for index in range(PIXEL_COUNT):
            path = self.state_dir / f"{index}.out"
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                signature = None

            if signature == self._output_signatures[index]:
                continue
            self._output_signatures[index] = signature

            if signature is None:
                self.strip.setPixelColor(index, self.color_factory(0, 0, 0))
                self.strip.show()
                continue

            try:
                config = parse_pixel_config(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                LOGGER.error("Cannot read %s: %s", path, error)
                continue

            red, green, blue = scaled_color(config)
            self.strip.setPixelColor(
                index, self.color_factory(red, green, blue)
            )
            self.strip.show()

    def sync_system_status(self) -> None:
        owntone_running = owntone_service_is_running()
        owntone_sync_running = owntone_sync_service_is_running()
        if owntone_running is None or owntone_sync_running is None:
            return

        if not owntone_running or not owntone_sync_running:
            on = True
            color = SYSTEM_PIXEL_RED
        else:
            wifi_connected = wifi_is_connected()
            if wifi_connected is None:
                return
            on = True
            color = SYSTEM_PIXEL_ORANGE if wifi_connected else SYSTEM_PIXEL_BLUE

        path = self.state_dir / "0.out"
        content = json.dumps(
            {
                "on": on,
                "color": color,
                "brightness": SYSTEM_PIXEL_BRIGHTNESS,
            },
            separators=(",", ":"),
        ) + "\n"
        try:
            if path.read_text(encoding="utf-8") == content:
                return
        except FileNotFoundError:
            pass

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_dir,
                prefix=".0.out.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def run(self) -> None:
        while True:
            self.sync_switch_files()
            self.sync_system_status()
            self.sync_pixel_files()
            time.sleep(POLL_INTERVAL)

    def turn_off_pixels(self) -> None:
        for index in range(PIXEL_COUNT):
            self.strip.setPixelColor(index, self.color_factory(0, 0, 0))
        self.strip.show()


_UNREAD = object()


class _GPIOInput:
    def __init__(self, gpio: object, pin: int) -> None:
        self._gpio = gpio
        self._pin = pin

    @property
    def is_pressed(self) -> bool:
        return self._gpio.input(self._pin) == self._gpio.LOW


def run_service(state_dir: Path) -> None:
    try:
        import RPi.GPIO as GPIO
        from rpi_ws281x import Color, PixelStrip
    except ImportError as error:
        raise RuntimeError(
            "Install RPi.GPIO and rpi_ws281x before running "
            "on the Raspberry Pi"
        ) from error

    strip = PixelStrip(
        PIXEL_COUNT,
        18,
        freq_hz=800000,
        dma=10,
        invert=False,
        brightness=255,
        channel=0,
    )
    strip.begin()
    buttons = [_GPIOInput(GPIO, pin) for pin in INPUT_PINS]
    service = StateService(state_dir, buttons, strip, Color)

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, handle_sigterm)
        GPIO.setmode(GPIO.BCM)
        for pin in INPUT_PINS:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        service.run()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        try:
            service.turn_off_pixels()
        finally:
            GPIO.cleanup(INPUT_PINS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("/var/lib/control-panel"),
        help="directory containing the .in and .out files",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args.state_dir.mkdir(parents=True, exist_ok=True)
    run_service(args.state_dir)


if __name__ == "__main__":
    main()
