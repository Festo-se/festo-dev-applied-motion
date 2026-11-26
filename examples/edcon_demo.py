# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Direct, configuration-driven demo for the Modbus/festo-edcon backend.

Edit the constants below for the bench. No command-line arguments are used.
The configurator-owned consolidated config contains the Modbus component ``gantry_2``.
Verify its illustrative addresses against the target bench before running. The config's
``axes`` mapping defines every axis. ``MOVEMENTS`` below defines move order.
"""

import json
import logging
from collections import deque

from configurator import load_consolidated_config
from applied_motion import Gantry


GANTRY_NAME = "gantry_2"
RUN_REFERENCING = False
HOME_GANTRY = False
MOVEMENTS = []  # Add one-axis movement dicts in desired execution order.

logger = logging.getLogger(__name__)


def main() -> None:
    """Connect to the configured gantry and run the selected demo actions."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    with Gantry(
        config=load_consolidated_config(),
        name=GANTRY_NAME,
        run_referencing=RUN_REFERENCING,
    ) as gantry:
        logger.info("Connected to gantry: %s", gantry)

        if HOME_GANTRY:
            gantry.home()

        if MOVEMENTS:
            gantry.move_to(deque(MOVEMENTS))

        print("Location:", json.dumps(gantry.get_location(), indent=2))
        print("Status:", json.dumps(gantry.get_status(), indent=2))


if __name__ == "__main__":
    main()
