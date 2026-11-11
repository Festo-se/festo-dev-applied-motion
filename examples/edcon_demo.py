# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Direct, configuration-driven demo for the Modbus/festo-edcon backend.

Edit the constants below for the bench. No command-line arguments are used.
The bundled ``gantry.json`` contains the Modbus component ``gantry_2``.
"""

import json
import logging

from collections import deque
from pathlib import Path

from applied_motion import Gantry


CONFIG_PATH = Path(__file__).resolve().parents[1] / "gantry.json"
GANTRY_NAME = "gantry_2"
RUN_REFERENCING = False
HOME_GANTRY = False
MOVE = None  # Example: {"X": {"position": 150.0, "velocity": 80.0}}

logger = logging.getLogger(__name__)


def main() -> None:
    """Connect to the configured gantry and run the selected demo actions."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    with Gantry(
        config=CONFIG_PATH,
        name=GANTRY_NAME,
        run_referencing=RUN_REFERENCING,
    ) as gantry:
        logger.info("Connected to gantry: %s", gantry)

        if HOME_GANTRY:
            gantry.home()

        if MOVE is not None:
            gantry.move_to(deque([MOVE]))

        print("Location:", json.dumps(gantry.get_location(), indent=2))
        print("Status:", json.dumps(gantry.get_status(), indent=2))


if __name__ == "__main__":
    main()
