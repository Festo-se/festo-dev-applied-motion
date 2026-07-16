"""Hello."""

import json
from os import getenv
from pathlib import Path
import logging
# from pgva import PGVA, PGVATCPConfig

from applied_motion import Gantry

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()],
)
am_file_handler = logging.FileHandler("applied_motion.log")
am_file_handler.setLevel(logging.DEBUG)
am_file_handler.setFormatter(logging.Formatter(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
am_logger = logging.getLogger("applied_motion")
am_logger.addHandler(am_file_handler)
am_logger.addHandler(logging.StreamHandler())

_DEFAULT_FPOSBAPI_IP = "192.168.0.50"
_DEFAULT_FPOSBAPI_PORT = 1234

ip = getenv("FPOSBAPI_IP", _DEFAULT_FPOSBAPI_IP)
port = int(getenv("FPOSBAPI_PORT", str(_DEFAULT_FPOSBAPI_PORT)))
fixture_path = Path(__file__).parent / "own-tester-config.json"
with fixture_path.open() as fh:
    cfg = json.load(fh)
    # TODO: Validate TCP connction with cfg["interface"]["type"] = "tcp/ip"

import pprint

pprint.pprint(cfg)
# pgva = PGVA(config=PGVATCPConfig(interface="tcp/ip", ip="192.168.10.102"))
# pgva.set_output_pressure(449)
# TODO: Fully enable teach tray functionality
gantry = Gantry.from_config(cfg, name="gantry_1")
# gantry.home()
cmds = gantry._client.list_commands()
pprint.pprint(f"pprint: {cmds=}")
print(f"cmds: {cmds}")
taught_pos = gantry._client.teach_pos(1, 1)
pprint.pprint(taught_pos)
# gantry._client.get_veab()
# gantry._client.set_veab(200)
# import time

# gantry._client.get_veab()

# time.sleep(1)
# gantry._client.get_veab()
