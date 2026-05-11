# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Interactive teach-in REPL for gantry position recording.

Requires the ``teach`` optional-dependency extra::

    pip install festo-dev-applied-motion[teach]

Launch via the installed entry point::

    applied-motion-teach --config gantry.json

Or directly::

    python -m applied_motion.teach_in.cli --config gantry.json

Commands
--------
The REPL accepts the following commands (tab-completion and command
history are provided by ``prompt_toolkit``):

======================================  ======================================
Command                                 Effect
======================================  ======================================
``jog``                                 Enter arrow-key jog mode (see below)
``jog <axis> <+/-> <step> [vel]``       Single step-move, then return to REPL
``where``                               Print current axis positions
``home``                                Home all axes
``capture <label>``                     Record current position as *label*
``teach pos <pos_id>``                  (FPosBAPI only) Send TEACH_POS to PLC
``teach tray <tray_id> <tray_pos>``     (FPosBAPI only) Send TEACH_TRAY to PLC
``list``                                List all captured positions
``save <path>``                         Write positions to a JSON file
``load <path>``                         Merge positions from a JSON file
``help``                                Print this command reference
``quit``                                Exit the REPL
======================================  ======================================

Jog mode key bindings
---------------------

=====================  ================================================
Key                    Action
=====================  ================================================
← / →                  Step axis[0] (typically X) negative / positive
↑ / ↓                  Step axis[1] (typically Y) positive / negative
Page Up / Page Down    Step axis[2] (typically Z) positive / negative
``+``                  Increase step size (cycles 0.1→0.5→1→5→10→50 mm)
``-``                  Decrease step size
``Esc`` or ``q``       Exit jog mode, return to REPL
=====================  ================================================
"""

import argparse
import logging
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console
from rich.table import Table, box

from applied_motion.gantry import Gantry
from applied_motion.teach_in.session import TeachSession

console = Console()
logger = logging.getLogger(__name__)

_STEP_SIZES = [0.1, 0.5, 1.0, 5.0, 10.0, 50.0]  # mm, cycled by +/-
_DEFAULT_STEP_IDX = 2  # 1.0 mm

_HELP_TEXT = """
[bold cyan]Commands[/]
  [green]jog[/]                               Enter arrow-key jog mode
  [green]jog[/] [yellow]<axis>[/] [yellow]<+/->[/] [yellow]<step>[/] [dim]\\[vel][/]   Single step-move (mm, default vel=10 mm/s)
  [green]where[/]                            Print current axis positions
  [green]home[/]                             Home all axes
  [green]capture[/] [yellow]<label>[/]                  Record current position as label
  [green]teach pos[/] [yellow]<pos_id>[/]               (FPosBAPI) TEACH_POS → PLC slot
  [green]teach tray[/] [yellow]<tray_id>[/] [yellow]<tray_pos>[/]   (FPosBAPI) TEACH_TRAY → PLC
  [green]list[/]                             List all captured positions
  [green]save[/] [yellow]<path>[/]                       Write positions to JSON
  [green]load[/] [yellow]<path>[/]                       Merge positions from JSON
  [green]help[/]                             Show this reference
  [green]quit[/]                             Exit
"""

_TOP_LEVEL_CMDS = [
    "jog",
    "where",
    "home",
    "capture",
    "teach",
    "list",
    "save",
    "load",
    "help",
    "quit",
    "exit",
]
_DIRECTIONS = ["+", "-"]


def _location_table(loc: dict[str, float]) -> Table:
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE, padding=(0, 1))
    table.add_column("Axis", style="bold")
    table.add_column("Position (mm)", justify="right")
    for axis, pos in loc.items():
        table.add_row(axis, f"{pos:.3f}")
    return table


def _positions_table(positions: dict[str, dict[str, float]]) -> Table:
    if not positions:
        return Table(show_header=False, box=None)
    axes = list(next(iter(positions.values())).keys())
    table = Table(show_header=True, header_style="bold cyan", box=box.SIMPLE, padding=(0, 1))
    table.add_column("Label", style="bold")
    for axis in axes:
        table.add_column(f"{axis} (mm)", justify="right")
    for label, pos in positions.items():
        table.add_row(label, *[f"{v:.3f}" for v in pos.values()])
    return table


def _build_completer(axis_names: list[str]) -> WordCompleter:
    return WordCompleter(
        _TOP_LEVEL_CMDS + axis_names + _DIRECTIONS,
        ignore_case=True,
        sentence=True,
    )


def _run_jog_mode(session: TeachSession, gantry: Gantry) -> None:  # noqa
    """Arrow-key driven inline jog TUI.  Press Esc or q to return to REPL."""
    axis_names = list(gantry.axes.keys())

    # Map directional keys → (axis_name, direction).  Up to 3 axes supported.
    _key_axis: dict[str, tuple[str, str]] = {}
    _key_help: list[str] = []
    if len(axis_names) >= 1:
        _key_axis["left"] = (axis_names[0], "-")
        _key_axis["right"] = (axis_names[0], "+")
        _key_help.append(f"  ←/→       {axis_names[0]}")
    if len(axis_names) >= 2:
        _key_axis["up"] = (axis_names[1], "+")
        _key_axis["down"] = (axis_names[1], "-")
        _key_help.append(f"  ↑/↓       {axis_names[1]}")
    if len(axis_names) >= 3:
        _key_axis["pageup"] = (axis_names[2], "+")
        _key_axis["pagedown"] = (axis_names[2], "-")
        _key_help.append(f"  PgUp/PgDn {axis_names[2]}")

    state: dict = {
        "step_idx": _DEFAULT_STEP_IDX,
        "status": ("fg:green", "Ready — use arrow keys to jog"),
        "location": gantry.get_location(),
    }

    def _content() -> FormattedText:
        step = _STEP_SIZES[state["step_idx"]]
        loc = state["location"]
        status_style, status_msg = state["status"]
        parts: list[tuple[str, str]] = [
            ("bold", "\n  ── Jog Mode ──  "),
            ("dim", "(Esc / q to exit)\n\n"),
            ("bold cyan", "  Position:\n"),
        ]
        for axis, pos in loc.items():
            parts += [
                ("", "    "),
                ("bold", f"{axis:<6}"),
                ("fg:white", f"{pos:>10.3f} mm\n"),
            ]
        parts += [
            ("", "\n"),
            ("bold cyan", "  Step: "),
            ("bold yellow", f"{step} mm"),
            ("dim", "  (+ to increase, - to decrease)\n\n"),
            ("dim", "  " + "    ".join(_key_help) + "\n\n"),
            (status_style, f"  {status_msg}\n"),
        ]
        return FormattedText(parts)

    kb = KeyBindings()

    def _do_jog(key: str) -> None:
        if key not in _key_axis:
            return
        axis, direction = _key_axis[key]
        step = _STEP_SIZES[state["step_idx"]]
        try:
            state["location"] = session.jog(axis, direction, step)
            state["status"] = ("fg:green", f"OK  {direction}{step:.3g} mm on {axis}")
        except (ValueError, KeyError) as exc:
            state["status"] = ("fg:red", f"Limit / config error: {exc}")
        except Exception as exc:
            logger.debug("jog error", exc_info=True)
            state["status"] = ("fg:red", f"Error: {exc}")

    for _k in ("left", "right", "up", "down", "pageup", "pagedown"):

        @kb.add(_k)
        def _(event, __k: str = _k) -> None:
            _do_jog(__k)

    @kb.add("+")
    def _(event) -> None:
        state["step_idx"] = min(state["step_idx"] + 1, len(_STEP_SIZES) - 1)
        state["status"] = ("fg:green", f"Step → {_STEP_SIZES[state['step_idx']]} mm")

    @kb.add("-")
    def _(event) -> None:
        state["step_idx"] = max(state["step_idx"] - 1, 0)
        state["status"] = ("fg:green", f"Step → {_STEP_SIZES[state['step_idx']]} mm")

    @kb.add("escape")
    @kb.add("q")
    def _(event) -> None:
        event.app.exit()

    layout = Layout(Window(content=FormattedTextControl(_content, focusable=True)))
    Application(layout=layout, key_bindings=kb, full_screen=False).run()
    console.print("[dim]Returned to REPL.[/]")


def run_repl(session: TeachSession, gantry: Gantry) -> None:  # noqa
    """Launch the interactive teach-in REPL for a connected gantry.

    Presents a prompt-toolkit REPL that accepts ``jog``, ``capture``,
    ``where``, ``home``, ``teach pos``, ``teach tray``, ``list``, ``save``,
    ``load``, ``help``, and ``quit`` commands.  Tab-completion and command
    history are provided automatically.

    Args:
        session: A :class:`~applied_motion.teach_in.session.TeachSession`
            instance backed by a connected, homed gantry.  Captured
            positions accumulate in ``session.positions``.
        gantry: The connected :class:`~applied_motion.gantry.Gantry`
            instance whose axes define the tab-completion candidates and
            receive motion commands.
    """
    axis_names = list(gantry.axes.keys())
    ps: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=_build_completer(axis_names),
    )

    console.print(_HELP_TEXT)

    while True:
        try:
            raw = ps.prompt("teach> ").strip()
        except (EOFError, KeyboardInterrupt):  # noqa
            console.print("\n[dim]Exiting.[/]")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit"):
                break

            elif cmd == "help":
                console.print(_HELP_TEXT)

            elif cmd == "where":
                loc = gantry.get_location()
                console.print(_location_table(loc))

            elif cmd == "home":
                gantry.home()
                console.print("[green]✓[/] All axes homed.")

            elif cmd == "jog":
                if len(parts) == 1:
                    # No args → enter arrow-key jog TUI
                    _run_jog_mode(session, gantry)
                elif len(parts) < 4:
                    console.print(
                        "[red]✗[/] Usage:\n"
                        "    [green]jog[/]                          Enter arrow-key jog mode\n"
                        "    [green]jog[/] [yellow]<axis> <+/-> <step>[/] [dim]\\[vel][/]   Single step"
                    )
                else:
                    axis = parts[1].upper()
                    direction = parts[2]
                    step = float(parts[3])
                    vel = float(parts[4]) if len(parts) > 4 else 10.0
                    try:
                        loc = session.jog(axis, direction, step, vel)
                        console.print(_location_table(loc))
                    except (ValueError, KeyError) as exc:
                        console.print(f"[red]✗[/] {exc}")
                    except Exception as exc:
                        logger.debug("jog error", exc_info=True)
                        console.print(f"[red]✗[/] Move rejected by axis: {exc}")

            elif cmd == "capture":
                if len(parts) < 2:
                    console.print("[red]✗[/] Usage: capture <label>")
                    continue
                label = parts[1]
                session.capture(label)
                console.print(f"[green]✓[/] Captured [bold]{label!r}[/]")

            elif cmd == "teach":
                if gantry._client is None:
                    console.print(
                        "[yellow]![/] [dim]Modbus backend — no PLC teach command available.[/]\n"
                        "    Use [green]capture[/] to save positions to JSON instead."
                    )
                elif len(parts) >= 3 and parts[1] == "pos":
                    pos_id = int(parts[2])
                    gantry._client.teach_pos(pos_id=pos_id)
                    console.print(f"[green]✓[/] TEACH_POS sent ([cyan]pos_id={pos_id}[/])")
                elif len(parts) >= 4 and parts[1] == "tray":
                    tray_id, tray_pos = int(parts[2]), int(parts[3])
                    gantry._client.teach_tray(tray_id=tray_id, tray_pos=tray_pos)
                    console.print(
                        f"[green]✓[/] TEACH_TRAY sent ([cyan]tray_id={tray_id}[/], [cyan]tray_pos={tray_pos}[/])"
                    )
                else:
                    console.print("[red]✗[/] Usage:\n    teach pos <pos_id>\n    teach tray <tray_id> <tray_pos>")

            elif cmd == "list":
                if not session.positions:
                    console.print("[dim]No positions captured yet.[/]")
                else:
                    console.print(_positions_table(session.positions))

            elif cmd == "save":
                if len(parts) < 2:
                    console.print("[red]✗[/] Usage: save <path>")
                    continue
                session.save(parts[1])
                console.print(f"[green]✓[/] {len(session.positions)} position(s) saved → [bold]{parts[1]}[/]")

            elif cmd == "load":
                if len(parts) < 2:
                    console.print("[red]✗[/] Usage: load <path>")
                    continue
                before = len(session.positions)
                session.load(parts[1])
                added = len(session.positions) - before
                console.print(f"[green]✓[/] {added} position(s) loaded from [bold]{parts[1]}[/]")

            else:
                console.print(f"[red]✗[/] Unknown command: [bold]{cmd!r}[/]  (type [green]help[/])")

        except (KeyError, ValueError, IndexError) as exc:
            console.print(f"[red]✗[/] {exc}")
        except Exception as exc:
            logger.exception("Unexpected error processing command %r", raw)
            console.print(f"[red]✗[/] Unexpected error: {exc}")


def main() -> None:
    """Entry point for the ``applied-motion-teach`` CLI command."""
    parser = argparse.ArgumentParser(
        prog="applied-motion-teach",
        description="Interactive teach-in REPL for gantry position recording.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to the gantry JSON configuration file.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        metavar="LEVEL",
        help="Python logging level (default: WARNING).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    console.print(f"[dim]Loading config:[/] {args.config}")
    try:
        gantry = Gantry.from_config(args.config)
    except Exception as exc:
        console.print(f"[red]✗[/] Failed to connect: {exc}")
        sys.exit(1)

    console.print(f"[green]✓[/] Connected: [bold]{gantry!r}[/]")

    # Wire FPosBAPI hook: after every capture, remind the operator to
    # optionally commit the position to the PLC with 'teach pos'.
    on_capture = None
    if gantry._client is not None:

        def on_capture(label: str, pos: dict[str, float]) -> None:
            console.print(f"  [dim]Tip: run [green]teach pos <id>[/] to commit [bold]{label!r}[/] to the PLC.[/]")

    session = TeachSession(gantry, on_capture=on_capture)
    run_repl(session, gantry)


if __name__ == "__main__":
    main()
