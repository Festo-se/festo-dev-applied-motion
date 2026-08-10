# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Composable command-registry framework shared by CLI packages."""

from applied_motion.cli.compose.core import (
    Command,
    CommandError,
    CommandGroup,
    CommandHandler,
    UnknownCommandError,
    UsageError,
)

__all__ = [
    "Command",
    "CommandError",
    "CommandGroup",
    "CommandHandler",
    "UnknownCommandError",
    "UsageError",
]
