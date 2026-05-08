"""Hello."""

import json
from os import getenv
from pathlib import Path
import logging
from pgva import PGVA, PGVATCPConfig

from applied_motion import Gantry

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler("applied_motion.log"), logging.StreamHandler()],
)

_DEFAULT_FPOSBAPI_IP = "192.168.10.25"
_DEFAULT_FPOSBAPI_PORT = 1234

ip = getenv("FPOSBAPI_IP", _DEFAULT_FPOSBAPI_IP)
port = int(getenv("FPOSBAPI_PORT", str(_DEFAULT_FPOSBAPI_PORT)))
fixture_path = Path(__file__).parent / "gantry.json"
with fixture_path.open() as fh:
    cfg = json.load(fh)
    cfg["connection"]["ip"] = ip
    cfg["connection"]["port"] = port

pgva = PGVA(config=PGVATCPConfig(interface="tcp/ip", ip="192.168.10.102"))
pgva.set_output_pressure(449)
gantry = Gantry.from_config(cfg)
gantry._client.get_veab()
gantry._client.set_veab(200)
import time

gantry._client.get_veab()

time.sleep(1)
gantry._client.get_veab()
