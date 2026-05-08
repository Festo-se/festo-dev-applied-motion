"""Hello."""

import json
import socket
from os import getenv
from pathlib import Path

from applied_motion import Gantry

_DEFAULT_FPOSAPI_IP = "192.168.10.25"
_DEFAULT_FPOSAPI_PORT = 1234

ip = getenv("FPOSAPI_IP", _DEFAULT_FPOSAPI_IP)
port = int(getenv("FPOSAPI_PORT", str(_DEFAULT_FPOSAPI_PORT)))
fixture_path = Path(__file__).parent / "gantry.json"
with fixture_path.open() as fh:
    cfg = json.load(fh)
    cfg["connection"]["ip"] = ip
    cfg["connection"]["port"] = port


gantry = Gantry.from_config(cfg)
gantry.home()
