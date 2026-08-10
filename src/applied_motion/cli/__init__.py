# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Interactive commissioning and teach-in tooling for gantry position recording.

[`TeachSession`][applied_motion.cli.session.TeachSession] is available without any extras — it has no
dependency on ``prompt_toolkit`` or ``rich``.

The interactive REPL ([`applied_motion.cli.cli`][applied_motion.cli.cli]) and the
``cli`` optional-dependency extra require the ``cli`` extra::

    pip install festo-dev-applied-motion[cli]

Typical usage::

    from applied_motion.cli import TeachSession

    session = TeachSession(gantry, on_capture=my_hook)
    session.jog("X", "+", 5.0)
    session.capture("deck_a1")
    session.save("deck_layout.json")

To launch the interactive REPL::

    applied-motion --config gantry.json
"""

from applied_motion.cli.session import TeachSession
from applied_motion.cli.cli import (
    build_standalone_motion_parser,
    dispatch_motion_command,
    register_motion_cli,
    run_repl,
)
from applied_motion.cli.compose import Command, CommandGroup, CommandHandler, UnknownCommandError, UsageError

__all__ = [
    "TeachSession",
    "run_repl",
    "register_motion_cli",
    "build_standalone_motion_parser",
    "dispatch_motion_command",
    "Command",
    "CommandGroup",
    "CommandHandler",
    "UnknownCommandError",
    "UsageError",
]
