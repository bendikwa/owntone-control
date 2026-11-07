#!/usr/bin/env python3
"""Mirror Owntone AirPlay output status to control-panel LED state files."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "http://127.0.0.1:3689"
DEFAULT_STATE_DIR = Path("/var/lib/control-panel")
DEFAULT_POLL_INTERVAL = 2.0
BRIGHTNESS = 0.1
AVAILABLE_COLOR = "#FFA500"
CONNECTED_COLOR = "#008000"
LOGGER = logging.getLogger("owntone_sync")


@dataclass(frozen=True)
class SyncConfig:
    api_url: str
    state_dir: Path
    poll_interval: float
    sources: tuple[str, ...]


def load_config(path: Path) -> SyncConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("configuration must be a JSON object")

    api_url = data.get("owntone_url", DEFAULT_API_URL)
    if not isinstance(api_url, str) or not api_url:
        raise ValueError("'owntone_url' must be a non-empty URL")
    parsed_url = urllib.parse.urlsplit(api_url)
    if (
        parsed_url.scheme not in ("http", "https")
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("'owntone_url' must be an HTTP(S) URL without credentials")
    api_url = api_url.rstrip("/")

    state_dir_value = data.get("state_dir", str(DEFAULT_STATE_DIR))
    if not isinstance(state_dir_value, str) or not state_dir_value:
        raise ValueError("'state_dir' must be a non-empty path")

    poll_interval = data.get("poll_interval", DEFAULT_POLL_INTERVAL)
    if (
        isinstance(poll_interval, bool)
        or not isinstance(poll_interval, (int, float))
        or not math.isfinite(poll_interval)
        or poll_interval <= 0
    ):
        raise ValueError("'poll_interval' must be a finite number greater than 0")

    sources = data.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        raise ValueError("'sources' must contain between 1 and 4 speaker names")
    if any(
        not isinstance(source, str) or not source or source.strip() != source
        for source in sources
    ):
        raise ValueError("speaker names must be non-empty strings without outer spaces")
    if len(set(sources)) != len(sources):
        raise ValueError("speaker names in 'sources' must be unique")

    return SyncConfig(
        api_url=api_url,
        state_dir=Path(state_dir_value),
        poll_interval=float(poll_interval),
        sources=tuple(sources),
    )


class OwntoneApi:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _request(self, method: str, endpoint: str, body: dict[str, Any] | None = None) -> Any:
        encoded_body = None
        headers = {"Accept": "application/json"}
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=encoded_body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response_body = response.read()
        if not response_body:
            return None
        return json.loads(response_body.decode("utf-8"))

    def get_outputs(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/outputs")
        if not isinstance(response, dict) or not isinstance(
            response.get("outputs"), list
        ):
            raise ValueError("Owntone returned an invalid outputs response")

        outputs = response["outputs"]
        for output in outputs:
            if (
                not isinstance(output, dict)
                or not isinstance(output.get("id"), str)
                or not isinstance(output.get("name"), str)
                or not isinstance(output.get("type"), str)
                or not isinstance(output.get("selected"), bool)
            ):
                raise ValueError("Owntone returned an invalid output entry")
        return outputs

    def select_output(self, output_id: str) -> None:
        endpoint = f"/api/outputs/{urllib.parse.quote(output_id, safe='')}"
        self._request("PUT", endpoint, {"selected": True})


class OwntoneSync:
    def __init__(self, config: SyncConfig, api: OwntoneApi) -> None:
        self.config = config
        self.api = api

    def sync_once(self) -> None:
        outputs = self.api.get_outputs()
        for index, source_name in enumerate(self.config.sources, start=1):
            matches = [
                output
                for output in outputs
                if output["type"] == "AirPlay" and output["name"] == source_name
            ]
            if len(matches) > 1:
                LOGGER.error(
                    "Multiple AirPlay outputs are named %r; LED %s will be off",
                    source_name,
                    index,
                )
                self._write_led(index, on=False, color=AVAILABLE_COLOR)
                continue
            if not matches:
                self._write_led(index, on=False, color=AVAILABLE_COLOR)
                continue

            output = matches[0]
            connected = output["selected"]
            if not connected and (self.config.state_dir / f"{index}.in").exists():
                try:
                    self.api.select_output(output["id"])
                    connected = True
                except (OSError, ValueError) as error:
                    LOGGER.error(
                        "Could not connect AirPlay speaker %r: %s", source_name, error
                    )

            self._write_led(
                index,
                on=True,
                color=CONNECTED_COLOR if connected else AVAILABLE_COLOR,
            )

        for index in range(len(self.config.sources) + 1, 5):
            self._write_led(index, on=False, color=AVAILABLE_COLOR)

    def _write_led(self, index: int, *, on: bool, color: str) -> None:
        path = self.config.state_dir / f"{index}.out"
        content = json.dumps(
            {"on": on, "color": color, "brightness": BRIGHTNESS},
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
                dir=self.config.state_dir,
                prefix=f".{path.name}.",
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
            try:
                self.sync_once()
            except (OSError, ValueError) as error:
                LOGGER.error("Owntone sync failed: %s", error)
            time.sleep(self.config.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/owntone-sync/config.json"),
        help="path to the local JSON configuration file",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    OwntoneSync(config, OwntoneApi(config.api_url)).run()


if __name__ == "__main__":
    main()
