# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""CLI demo for the FPosBAPI backend.

COMMISSIONING AND TESTING ONLY: this script connects to real hardware. It can
home, teach, or move axes when explicitly requested. Verify machine safety and
the selected configuration before running it.

The config's ``axes`` mapping defines every axis. The PLC homes all axes in one command, while CLI move
options explicitly select the axis and target.
"""

import argparse
import json
import logging

from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build the commissioning/testing-only command-line parser."""
    parser = argparse.ArgumentParser(
        description="FPosBAPI gantry demo — commissioning/testing only; connects to real hardware.",
        epilog=(
            "COMMISSIONING AND TESTING ONLY. This script can command real motion and teach PLC positions. "
            "Verify the target configuration, machine clearance, and move limits before use."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=Path, required=True, help="Path to the gantry JSON configuration")
    parser.add_argument("--component", default="gantry_1", help="Configured gantry component name")
    parser.add_argument("--home", action="store_true", help="Home the gantry after connecting")
    parser.add_argument("--show-status", action="store_true", help="Print gantry status")
    parser.add_argument("--list-commands", action="store_true", help="Print supported PLC commands")
    parser.add_argument("--teach-pos", type=int, help="Teach the current pose into this PLC position ID")
    parser.add_argument("--move-axis", help="Axis label for one move, for example X")
    parser.add_argument("--position", type=float, help="Target position in mm")
    parser.add_argument("--velocity", type=float, help="Target velocity in mm/s")
    parser.add_argument("--relative", action="store_true", help="Use relative rather than absolute movement")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="INFO",
        help="Logging level",
    )
    return parser


def main() -> None:
    """Run selected FPosBAPI commissioning/testing actions."""
    parser = _build_parser()
    args = parser.parse_args()

    move_args = (args.move_axis, args.position, args.velocity)
    if any(value is not None for value in move_args) and any(value is None for value in move_args):
        parser.error("--move-axis, --position, and --velocity must be provided together")
    if args.relative and all(value is None for value in move_args):
        parser.error("--relative requires a complete move specification")

    from applied_motion import Gantry

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    with Gantry(config=args.config, name=args.component) as gantry:
        logger.info("Connected to gantry: %s", gantry)

        if args.home:
            logger.info("Homing gantry")
            gantry.home()

        if args.move_axis is not None:
            position_type = "relative" if args.relative else "absolute"
            movement = {
                args.move_axis: {
                    "position": args.position,
                    "velocity": args.velocity,
                    "position_type": position_type,
                }
            }
            logger.info("Moving %s (%s)", args.move_axis, position_type)
            gantry.move_to(deque([movement]))

        if args.teach_pos is not None:
            if not gantry.supports_teach():
                raise RuntimeError("Configured backend does not support teach operations")
            logger.info("Teaching current pose to position ID %d", args.teach_pos)
            gantry.teach_pos(args.teach_pos)

        if args.list_commands:
            print("Supported PLC commands:")
            for command_name in gantry.list_commands():
                print(f"- {command_name}")

        if args.show_status:
            print(json.dumps(gantry.get_status(), indent=2))


if __name__ == "__main__":
    main()
