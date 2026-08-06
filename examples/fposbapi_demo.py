# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Minimal hardware-oriented demo for the FPosBAPI backend.

This script is intentionally simple and explicit:

- loads a gantry from a JSON config,
- optionally homes the machine,
- optionally executes one move,
- optionally prints full status and command list,
- optionally teaches a PLC position slot.

Use it as a starting point for bring-up and bench validation.
"""

import argparse
import json
import logging

from collections import deque
from pathlib import Path

from applied_motion import Gantry


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the demo.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(description="Run a basic FPosBAPI gantry demo")
    parser.add_argument("--config", type=Path, required=True, help="Path to JSON config file")
    parser.add_argument("--component", default="gantry_1", help="Component name inside config")
    parser.add_argument("--home", action="store_true", help="Home gantry before other operations")
    parser.add_argument("--show-status", action="store_true", help="Print Gantry.get_status() JSON")
    parser.add_argument("--list-commands", action="store_true", help="Print PLC command list")
    parser.add_argument("--teach-pos", type=int, default=None, help="Teach current pose into given position id")

    parser.add_argument("--move-axis", type=str, default=None, help="Axis label for one move (e.g. X)")
    parser.add_argument("--position", type=float, default=None, help="Target position in mm")
    parser.add_argument("--velocity", type=float, default=None, help="Target velocity in mm/s")
    parser.add_argument(
        "--relative",
        action="store_true",
        help="Use relative motion (default is absolute)",
    )

    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser.parse_args()


def maybe_move(gantry: Gantry, args: argparse.Namespace) -> None:
    """Execute one optional move if full move arguments are present.

    Args:
        gantry: Constructed gantry instance.
        args: Parsed CLI args.

    Raises:
        ValueError: If a partial move argument set is provided.
    """
    move_args = (args.move_axis, args.position, args.velocity)
    if all(value is None for value in move_args):
        return

    if any(value is None for value in move_args):
        raise ValueError("Provide --move-axis, --position, and --velocity together")

    position_type = "relative" if args.relative else "absolute"
    movements = deque(
        [
            {
                args.move_axis: {
                    "position": args.position,
                    "velocity": args.velocity,
                    "position_type": position_type,
                }
            }
        ]
    )
    logger.info("Moving %s to %.3f mm at %.3f mm/s (%s)", args.move_axis, args.position, args.velocity, position_type)
    gantry.move_to(movements)


def main() -> None:
    """Run the demo workflow."""
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    with Gantry.from_config(args.config, name=args.component) as gantry:
        logger.info("Connected to gantry: %s", gantry)

        if args.home:
            logger.info("Homing gantry")
            gantry.home()

        maybe_move(gantry, args)

        if args.teach_pos is not None:
            if not gantry.supports_teach():
                raise RuntimeError("Configured backend does not support teach operations")
            logger.info("Teaching current pose into position slot %d", args.teach_pos)
            gantry.teach_pos(args.teach_pos)

        if args.list_commands:
            print("Supported PLC commands:")
            for command_name in gantry.list_commands():
                print(f"- {command_name}")

        if args.show_status:
            print(json.dumps(gantry.get_status(), indent=2))


if __name__ == "__main__":
    main()
