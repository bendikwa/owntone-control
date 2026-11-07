# Owntone Control: control-panel

The control-panel component is a Raspberry Pi service that mirrors four
switches to files and uses five JSON files to control five pixels in one
WS281x-compatible LED chain.

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

The default state directory is `/var/lib/control-panel`; pass `--state-dir` to
use another directory. The service creates it if needed.

* `1.in` through `4.in`: present means the corresponding switch is closed/on;
  absent means open/off. The service creates and removes these files.
* `0.out` through `4.out`: each file controls the pixel at the matching
  zero-based chain position. A missing file means that pixel is off.

The control-panel service updates pixel 0 to show system status at brightness
`0.1`: orange (`#FFA500`) when Owntone and Wi-Fi are connected, blue (`#000080`)
when both Owntone services are running but Wi-Fi is not connected, and red
(`#800000`) when either Owntone or owntone-sync is not running. Wi-Fi status is
read from the wireless network interface's Linux carrier state.

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

## Install as a systemd service

Download or clone this repository as your normal user. On Raspberry Pi OS,
install the hardware libraries (package availability may depend on your OS
release):

```sh
sudo apt install python3-rpi.gpio python3-rpi-ws281x
```

Install the component and its example unit from the repository root:

```sh
sudo install -D -o root -g root -m 0644 control_panel.py /opt/control-panel/control_panel.py
sudo install -D -o root -g root -m 0644 systemd/control-panel.service /etc/systemd/system/control-panel.service
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0755 /var/lib/control-panel
sudo systemctl daemon-reload
sudo systemctl enable --now control-panel.service
```

The unit runs as root because the WS281x driver needs root hardware access.
The installed program is root-owned under `/opt/control-panel`, rather than
being executed directly from the user-writable download. The service creates
and writes its state files in `/var/lib/control-panel`. That directory is
owned by the account running the install commands so that account (and any
other component running as it) can read switch files and write pixel settings.
Check the service status and logs with:

```sh
sudo systemctl status control-panel.service
sudo journalctl -u control-panel.service
```

After updating the downloaded source, repeat the `sudo install` command for
`control_panel.py`, then restart the service:

```sh
sudo systemctl restart control-panel.service
```

Switch inputs are read by polling `RPi.GPIO`; the service does not use a
gpiozero pin factory or configure GPIO edge detection.
The service turns all pixels off on shutdown.

For development, the hardware-independent tests run with:

```sh
python3 -m unittest discover -s tests
```

## Owntone AirPlay sync

`owntone_sync.py` is an independent service that polls Owntone's JSON API and
updates the `1.out` through `4.out` LED state files in the control-panel state
directory. It matches configured names against available `AirPlay` outputs:

* Available but not selected: orange (`#FFA500`) at brightness `0.1`.
* Selected in Owntone: green (`#008000`) at brightness `0.1`.
* Not available: off.

The order of names in `sources` maps directly to switch and LED numbers, so the
first source uses `1.in` and `1.out`, and so on. When a matching source is
available and its `.in` switch file exists, the service selects that Owntone
output via `PUT /api/outputs/{id}`. Output selection is not undone when the
switch turns off.

Copy [owntone-sync.example.json](./owntone-sync.example.json) to
`/etc/owntone-sync/config.json` and change the source names to exactly match
the AirPlay output names shown by Owntone. `owntone_url`, `state_dir`, and
`poll_interval` can also be set in this JSON file. The default API URL is
`http://127.0.0.1:3689`; the default state directory is
`/var/lib/control-panel`. Set `log_level` to `DEBUG`, `INFO`, `WARNING`,
`ERROR`, or `CRITICAL` to control service logging; it defaults to `INFO`.
Use `DEBUG` to see per-sync source status and LED-file updates, then inspect
the output with `sudo journalctl -u owntone-sync.service -f`.

Install the service and its example systemd unit:

```sh
sudo install -D -o root -g root -m 0644 owntone_sync.py /opt/owntone-sync/owntone_sync.py
sudo install -D -o root -g root -m 0644 systemd/owntone-sync.service /etc/systemd/system/owntone-sync.service
sudo install -D -o root -g root -m 0644 owntone-sync.example.json /etc/owntone-sync/config.json
sudo chown root:root /etc/owntone-sync/config.json
sudo chmod 0644 /etc/owntone-sync/config.json
sudo install -d -o owntone -g owntone -m 0755 /var/lib/control-panel
sudo systemctl daemon-reload
sudo systemctl enable --now owntone-sync.service
```

The sync service runs as the existing `owntone` account. The control
panel runs with root access to the state directory, while the sync account owns
the directory so it can write LED files and read switch files. Verify it with:

```sh
sudo systemctl status owntone-sync.service
sudo journalctl -u owntone-sync.service
```
