# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Generic prompt_toolkit REPL driver for command-group trees."""

import logging

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from applied_motion.cli.compose.core import CommandError, CommandGroup, UnknownCommandError, UsageError

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
    lines = ["[bold cyan]Commands[/]"]
    lines.extend(f"  {line}" for line in root.format_help())
    lines.append("  help    Show this reference")
    lines.append("  quit    Exit")
    return "\n".join(lines)


def run_repl(  # noqa: C901
    root: CommandGroup,
    prompt: str = "> ",
    console: Console | None = None,
    intro: bool = True,
) -> None:
    """Run an interactive REPL over a command tree."""
    console = console or Console()
    session: PromptSession[str] = PromptSession(history=InMemoryHistory(), completer=NamespaceCompleter(root))

    if intro:
        console.print(render_help(root))

    while True:
        try:
            raw = session.prompt(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Exiting.[/]")
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
            console.print(f"[red]✗[/] {exc}")
        except UnknownCommandError as exc:
            console.print(f"[red]✗[/] {exc}  (type [green]help[/])")
        except NotImplementedError as exc:
            console.print(f"[yellow]![/] Not supported: {exc}")
        except AttributeError as exc:
            console.print(f"[yellow]![/] Not available: {exc}")
        except (KeyError, ValueError, IndexError, RuntimeError, CommandError) as exc:
            console.print(f"[red]✗[/] {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error processing command %r", raw)
            console.print(f"[red]✗[/] Unexpected error: {exc}")


__all__ = ["NamespaceCompleter", "render_help", "run_repl"]
