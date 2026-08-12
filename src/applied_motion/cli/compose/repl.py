# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Generic prompt_toolkit REPL driver for command-group trees."""

import logging

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from applied_motion.cli.compose.core import CommandError, CommandGroup, UnknownCommandError, UsageError
from applied_motion.cli.formatting import format_group_header, format_help_line
from applied_motion.cli.theme import festo_console

logger = logging.getLogger(__name__)

_RESERVED_CMDS = ["help", "quit", "exit"]


class NamespaceCompleter(Completer):
    """Hierarchical tab-completer for a command-group tree."""

    def __init__(self, root: CommandGroup) -> None:
        """Initialise the completer for a command tree.

        Args:
            root: The root command-group to complete against.
        """
        self.root = root

    def get_completions(self, document, complete_event):  # noqa: ANN001, ANN201
        """Yield completion candidates for the current input.

        Args:
            document: The current input document.
            complete_event: Completion event object.
        """
        text = document.text_before_cursor
        tokens = text.split()
        at_word_boundary = text == "" or text.endswith(" ")
        consumed = tokens if at_word_boundary else tokens[:-1]
        partial = "" if at_word_boundary else tokens[-1].lower()

        group = self.root
        for token in consumed:
            key = token.lower()
            if key in group.children:
                group = group.children[key]
            else:
                return

        options = list(group.children.keys()) + list(group.commands.keys())
        if group is self.root:
            options = options + _RESERVED_CMDS
        for option in options:
            if option.startswith(partial):
                yield Completion(option, start_position=-len(partial))


def render_help(root: CommandGroup) -> str:
    """Render an aggregated help listing for a command tree."""
    lines = ["[festo.brand]Commands[/]"]

    def _append(group: CommandGroup, indent: int) -> None:
        for cmd in group.commands.values():
            lines.append(format_help_line(cmd.usage or cmd.name, cmd.help, indent=indent))
        for child_name, child in group.children.items():
            lines.append(format_group_header(child_name, child.help, indent=indent))
            _append(child, indent + 1)

    _append(root, 0)
    lines.append(format_help_line("help", "Show this reference"))
    lines.append(format_help_line("quit", "Exit"))
    return "\n".join(lines)


def run_repl(  # noqa: C901
    root: CommandGroup,
    prompt: str = "> ",
    console: Console | None = None,
    intro: bool = True,
) -> None:
    """Run an interactive REPL over a command tree."""
    console = console or festo_console()
    session: PromptSession[str] = PromptSession(history=InMemoryHistory(), completer=NamespaceCompleter(root))

    if intro:
        console.print(render_help(root))

    with patch_stdout(raw=True):
        while True:
            try:
                raw = session.prompt(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[festo.muted]Exiting.[/]")
                break

            if not raw:
                continue

            tokens = raw.split()
            head = tokens[0].lower()

            if head in ("quit", "exit"):
                break
            if head == "help":
                console.print(render_help(root))
                continue

            try:
                root.dispatch(tokens)
            except UsageError as exc:
                console.print(f"[festo.err]✗[/] {exc}")
            except UnknownCommandError as exc:
                console.print(f"[festo.err]✗[/] {exc}  (type [festo.ok]help[/])")
            except NotImplementedError as exc:
                console.print(f"[festo.warn]![/] Not supported: {exc}")
            except AttributeError as exc:
                console.print(f"[festo.warn]![/] Not available: {exc}")
            except (KeyError, ValueError, IndexError, RuntimeError, CommandError) as exc:
                console.print(f"[festo.err]✗[/] {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error processing command %r", raw)
                console.print(f"[festo.err]✗[/] Unexpected error: {exc}")


__all__ = ["NamespaceCompleter", "render_help", "run_repl"]
