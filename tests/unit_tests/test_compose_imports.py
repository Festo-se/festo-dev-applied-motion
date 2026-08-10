# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

from applied_motion.cli.compose import Command, CommandGroup


def test_command_group_imports_and_dispatches() -> None:
    """The shared compose package should be importable from applied_motion."""
    seen: list[str] = []

    child = CommandGroup("child")
    child.add_command(Command("ping", lambda tokens: seen.append("|".join(tokens))))

    root = CommandGroup("root")
    root.add_child(child)

    root.dispatch(["child", "ping", "one", "two"])

    assert seen == ["one|two"]
