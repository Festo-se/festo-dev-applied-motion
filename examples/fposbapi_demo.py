# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Direct, configuration-driven demo for the FPosBAPI backend.

Edit the constants below for the bench. No command-line arguments are used.
The bundled ``gantry.json`` contains the FPosBAPI component ``gantry_1``.
The config's ``axes`` mapping defines every axis. The PLC homes all axes together, and ``MOVEMENTS`` below
defines the order for queued moves.
"""

import json
import logging

from collections import deque
from pathlib import Path

from applied_motion import Gantry


CONFIG_PATH = Path(__file__).resolve().parents[1] / "gantry.json"
GANTRY_NAME = "gantry_1"
HOME_GANTRY = False
MOVEMENTS = []  # Add one-axis movement dicts in desired execution order.
TEACH_POSITION = None  # Set an integer position ID to teach the current pose.
LIST_COMMANDS = False

logger = logging.getLogger(__name__)


def main() -> None:
    """Connect to the configured gantry and run the selected demo actions."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    with Gantry(config=CONFIG_PATH, name=GANTRY_NAME) as gantry:
        logger.info("Connected to gantry: %s", gantry)

        if HOME_GANTRY:
            gantry.home()

        if MOVEMENTS:
            gantry.move_to(deque(MOVEMENTS))

        if TEACH_POSITION is not None:
            if not gantry.supports_teach():
                raise RuntimeError("Configured backend does not support teach operations")
            gantry.teach_pos(TEACH_POSITION)

        if LIST_COMMANDS:
            print("Supported PLC commands:")
            for command_name in gantry.list_commands():
                print(f"- {command_name}")

        print("Status:", json.dumps(gantry.get_status(), indent=2))


if __name__ == "__main__":
    main()
