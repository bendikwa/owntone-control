# GPIO and NeoPixel state service

A small Raspberry Pi service that mirrors four switches to files and uses five
JSON files to control five pixels in one WS281x-compatible LED chain.

## Wiring (Raspberry Pi Zero W)

Use BCM GPIO numbers in software:

| Purpose | BCM GPIO | Header pin |
| --- | ---: | ---: |
| Switch 1 | 17 | 11 |
| Switch 2 | 27 | 13 |
| Switch 3 | 22 | 15 |
| Switch 4 | 23 | 16 |
| NeoPixel data | 18 | 12 |
| Ground | — | 6 (or another GND pin) |

Wire each switch between its GPIO and ground. The service enables the GPIO
internal pull-up, so an open switch reads high and a closed switch reads 0 V
(electrical LOW). A closed switch is represented as **on** in its `.in` file.
This is the safe, conventional way to read a switch that pulls the input to
ground; Raspberry Pi GPIO pins are 3.3 V only and must never receive 5 V.

The five NeoPixels should be daisy-chained (`DIN` to GPIO18, then pixel-to-pixel);
the service controls them as one chain, not as five separate GPIO outputs.
Power the LEDs from a suitable external 5 V supply, not from a GPIO pin. Join
the LED supply ground to a Pi ground. A 330–470 ohm resistor in series with the
data line and a 1000 µF capacitor across the LED supply near the first pixel
are recommended. GPIO18 is commonly used for WS281x output on the Pi Zero W;
the LED data input must accept 3.3 V logic as stated for your LEDs.

## State files

The default state directory is `/var/lib/gpio-neopixel`; pass `--state-dir` to
use another directory. The service creates it if needed.

* `1.in` through `4.in`: present means the corresponding switch is closed/on;
  absent means open/off. The service creates and removes these files.
* `0.out` through `4.out`: each file controls the pixel at the matching
  zero-based chain position. A missing file means that pixel is off.

Each `.out` file contains a JSON object with all three fields:

```json
{
  "on": true,
  "color": "#33AAFF",
  "brightness": 0.4
}
```

`color` is an RGB hex string (`#RRGGBB`); `brightness` is a number from `0.0`
(dark) to `1.0` (full); `on` must be a boolean. For example, `0.out` controls
the first pixel and `4.out` the fifth. The service rejects malformed values,
logs the error, and leaves that pixel at its last valid setting. Write updates
to a temporary file in the same directory and rename it over the `.out` file
to avoid reading a partially written JSON document.

## Install and run

On Raspberry Pi OS, install the hardware libraries (package availability may
depend on your OS release):

```sh
sudo apt install python3-gpiozero python3-lgpio python3-rpi-ws281x
sudo python3 gpio_neopixel.py --state-dir /var/lib/gpio-neopixel
```

The service uses gpiozero's `lgpio` pin factory for switch inputs; installing
`RPi.GPIO` alone does not provide that backend.
Run with the permissions required by your OS's WS281x driver. Stop with
Ctrl+C; the service turns all pixels off on shutdown.

For development, the hardware-independent tests run with:

```sh
python3 -m unittest discover -s tests
```
