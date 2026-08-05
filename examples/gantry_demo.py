# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Manual hardware demo for the Modbus (festo-edcon) gantry backend.

This is a standalone example script — not part of the importable library.
It connects to real Festo CMMT drives at hardcoded IP addresses, issues a
sequence of moves, and exercises fault handling.  Adjust the IP addresses to
match your bench before running::

    python examples/gantry_demo.py

.. warning::
    This script commands real motion.  Ensure the axes are clear and it is
    safe to move before running.
"""

import logging
import time

from collections import deque

from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.gantry import Gantry

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the interactive festo-edcon gantry demo."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    x_axis = EdconAxis(name="X", ip="192.168.0.100", run_referencing=True)
    y_axis = EdconAxis(name="Y", ip="192.168.0.101")
    zg_axis = EdconAxis(name="ZG", ip="192.168.0.102")
    zp_axis = EdconAxis(name="ZP", ip="192.168.0.103")

    try:
        gantry = Gantry(axes={"X": x_axis, "Y": y_axis, "ZG": zg_axis, "ZP": zp_axis})

        moves = deque(
            [
                {
                    "X": {
                        "position": 192.23012889999998,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "Y": {
                        "position": 147.08970470000003,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "ZP": {
                        "position": 0.0,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "ZG": {
                        "position": 64.84794609999999,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
            ]
        )

        gantry.move_to(moves)

        params = {"position": 20, "velocity": 50}
        move_result = x_axis.move(params["position"], params["velocity"])
        logger.info("Move result: %s", move_result)

        params["position"] = 10
        move_result = x_axis.move(params["position"], params["velocity"])

        time.sleep(0.5)
        if x_axis.fault_present():
            logger.warning("Fault present!")
        logger.info(x_axis.fault_string())

        while True:
            params = {"position": 20, "velocity": 10}
            move_result = x_axis.move(**params)
            logger.info("Move result: %s", move_result)
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)

            params["position"] = 10
            move_result = x_axis.move(**params)
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)

    except KeyboardInterrupt:
        logger.info("Exiting...")
        try:
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)
            x_axis.stop_motion_task()
        except Exception as e:
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)
            logger.error("Error stopping motion task: %s", e)
        logger.info("Current position: %s", x_axis.position_info_string())
        logger.debug("xist_a=%s", x_axis.telegram.xist_a)
        x_axis.shutdown()


if __name__ == "__main__":
    main()
