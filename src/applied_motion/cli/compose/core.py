# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Composable, transportable command-registry core for interactive CLIs."""

import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CommandHandler = Callable[[Sequence[str]], None]


class CommandError(Exception):
    """Base error for command dispatch or execution failures."""


class UsageError(CommandError):
    """Raise when a command is invoked with missing or invalid arguments."""


class UnknownCommandError(CommandError):
    """Raise when a token matches neither a child group nor a local command."""


@dataclass
class Command:
    """A single named leaf command in a command group."""

    name: str
    handler: CommandHandler
    usage: str = ""
    help: str = ""
    completions: Callable[[], list[str]] | None = None

    def __repr__(self) -> str:
        """Return a readable representation of the command."""
        return f"Command(name={self.name!r})"


class CommandGroup:
    """A composable namespace of commands and nested child groups."""

    def __init__(self, name: str, help: str = "") -> None:
        """Initialise an empty command group.

        Args:
            name: The group's own name.
            help: Short description of the group.
        """
        self.name = name
        self.help = help
        self.commands: dict[str, Command] = {}
        self.children: dict[str, CommandGroup] = {}
        logger.debug("CommandGroup created: name=%s", name)

    def add_command(self, command: Command) -> Command:
        """Register a leaf command on this group.

        Args:
            command: The command to register.

        Returns:
            The registered command.
        """
        self.commands[command.name.lower()] = command
        return command

    def add_child(self, group: "CommandGroup", name: str | None = None) -> "CommandGroup":
        """Mount a child group under a name.

        Args:
            group: The child group to mount.
            name: Mount key. Defaults to the child group's name.

        Returns:
            The mounted child group.
        """
        key = (name or group.name).lower()
        self.children[key] = group
        logger.debug("CommandGroup %s mounted child under %r", self.name, key)
        return group

    def dispatch(self, tokens: Sequence[str]) -> None:
        """Route a token sequence to a child group or a local command.

        Args:
            tokens: The whitespace-split command line.

        Raises:
            UnknownCommandError: If no matching child or command is found.
        """
        if not tokens:
            return
        head = tokens[0].lower()
        rest = tokens[1:]
        if head in self.children:
            self.children[head].dispatch(rest)
            return
        if head in self.commands:
            self.commands[head].handler(rest)
            return
        raise UnknownCommandError(f"Unknown command: {tokens[0]!r}")

    def iter_paths(self, prefix: Sequence[str] = ()) -> Iterator[tuple[str, ...]]:
        """Yield the fully-qualified path of every command in the tree.

        Args:
            prefix: Internal prefix accumulator.

        Yields:
            Tuples of namespace names ending in a command name.
        """
        for name in self.commands:
            yield (*tuple(prefix), name)
        for child_name, child in self.children.items():
            yield from child.iter_paths((*tuple(prefix), child_name))

    def format_help(self, indent: int = 0) -> list[str]:
        """Build indented help lines for this group and all descendants.

        Args:
            indent: Current indentation depth.

        Returns:
            A list of formatted help lines.
        """
        pad = "  " * indent
        lines: list[str] = []
        for cmd in self.commands.values():
            usage = cmd.usage or cmd.name
            suffix = f"    {cmd.help}" if cmd.help else ""
            lines.append(f"{pad}{usage}{suffix}")
        for child_name, child in self.children.items():
            header = f"{pad}{child_name}"
            if child.help:
                header = f"{header}    {child.help}"
            lines.append(header)
            lines.extend(child.format_help(indent + 1))
        return lines

    def __contains__(self, name: object) -> bool:
        """Return whether a name is a local command or child group name."""
        if not isinstance(name, str):
            return False
        key = name.lower()
        return key in self.commands or key in self.children

    def __len__(self) -> int:
        """Return the number of local commands plus child groups."""
        return len(self.commands) + len(self.children)

    def __repr__(self) -> str:
        """Return a readable representation of the group."""
        return f"CommandGroup(name={self.name!r}, commands={len(self.commands)}, children={len(self.children)})"


__all__ = [
    "Command",
    "CommandError",
    "CommandGroup",
    "CommandHandler",
    "UnknownCommandError",
    "UsageError",
]
