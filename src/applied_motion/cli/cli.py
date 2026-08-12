# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Interactive commissioning and teach-in REPL for gantry position recording.

Requires the ``cli`` optional-dependency extra::

    pip install festo-dev-applied-motion[cli]

Launch via the installed entry point::

    applied-motion --config gantry.json

Or directly::

    python -m applied_motion.cli.cli --config gantry.json

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
``teach pos <pos_id>``                  (FPosBAPI only) Send TEACH_POS to PLC # TODO: INclude tool id
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
``Tab``                Cycle PgUp/PgDn target to next depth axis (3+ axes)
``Shift+Tab``          Cycle PgUp/PgDn target to previous depth axis
``Esc`` or ``q``       Exit jog mode, return to REPL
=====================  ================================================
"""

# TODO: cli tool fails with no notification why when gantry is not referenced/homed. Fix this
import argparse
import logging
import sys
import threading
from collections.abc import Callable, Sequence
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
from rich.table import Table, box

from applied_motion.cli.formatting import build_help_block, format_badge, format_bool, format_help_line
from applied_motion.cli.theme import (
    FESTO_BLUE_2,
    FESTO_BLUE_CAERUL,
    FESTO_GRAY_SUCANUL,
    FESTO_GRAY_CANUL,
    FESTO_SIGNAL_ORANGE,
    FESTO_SIGNAL_RED,
    festo_console,
)

from applied_motion.applied_motion import Gantry
from applied_motion.cli.session import TeachSession

console = festo_console()
logger = logging.getLogger(__name__)

_STEP_SIZES = [0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0]  # mm, cycled by +/-
_DEFAULT_STEP_IDX = 2  # 1.0 mm

_HELP_ENTRIES: list[tuple[str, str]] = [
    ("jog", "Enter arrow-key jog mode"),
    ("jog <axis> <+/-> <step> [vel]", "Single step-move (mm, default vel=10 mm/s)"),
    ("where", "Print current axis positions"),
    ("home", "Home all axes"),
    ("capture <label>", "Record current position as label"),
    ("teach pos <pos_id>", "(FPosBAPI) TEACH_POS → PLC slot"),
    ("teach tray <tray_id> <tray_pos>", "(FPosBAPI) TEACH_TRAY → PLC"),
    ("list", "List all captured positions"),
    ("save <path>", "Write positions to JSON"),
    ("load <path>", "Merge positions from JSON"),
    ("help", "Show this reference"),
    ("quit", "Exit"),
]

_HELP_TEXT = build_help_block(_HELP_ENTRIES)

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

MotionCliExtension = Callable[[argparse._SubParsersAction], None]
"""Extension hook signature for adding extra motion subcommands.

Each extension receives motion command's subparser collection and may call
``add_parser`` to register additional nested commands.
"""


def _location_table(loc: dict[str, float]) -> Table:
    table = Table(
        show_header=True, header_style="festo.brand", box=box.SIMPLE_HEAD, padding=(0, 1), border_style=FESTO_BLUE_2
    )
    table.add_column("Axis", style="festo.ok")
    table.add_column("Position (mm)", justify="right", style="festo.value")
    for axis, pos in loc.items():
        table.add_row(axis, f"{pos:.3f}")
    return table


def _positions_table(positions: dict[str, dict[str, float]]) -> Table:
    if not positions:
        return Table(show_header=False, box=None)
    axes = list(next(iter(positions.values())).keys())
    table = Table(
        show_header=True,
        header_style="festo.brand",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        border_style=FESTO_BLUE_2,
        row_styles=["", "festo.muted"],
    )
    table.add_column("Label", style="festo.ok")
    for axis in axes:
        table.add_column(f"{axis} (mm)", justify="right", style="festo.value")
    for label, pos in positions.items():
        table.add_row(label, *[f"{v:.3f}" for v in pos.values()])
    return table


def _build_completer(axis_names: list[str]) -> WordCompleter:
    return WordCompleter(
        _TOP_LEVEL_CMDS + axis_names + _DIRECTIONS,
        ignore_case=True,
        sentence=True,
    )  # TODO: Move to cli composer util?


def _run_jog_mode(session: TeachSession, gantry: Gantry) -> None:  # noqa
    """Arrow-key driven inline jog TUI.  Press Esc or q to return to REPL."""
    axis_names = list(gantry.axes.keys())
    # axes[2:] are depth axes — cycled via Tab, stepped via PgUp/PgDn
    depth_axes = axis_names[2:]

    state: dict = {
        "step_idx": _DEFAULT_STEP_IDX,
        "status": ("ok", "Ready. Use arrow keys to jog."),
        "location": gantry.get_location(),
        "depth_idx": 0,
        "busy": False,
    }

    def _active_depth() -> str | None:
        return depth_axes[state["depth_idx"]] if depth_axes else None

    def _content() -> FormattedText:
        step = _STEP_SIZES[state["step_idx"]]
        loc = state["location"]
        status_tone, status_msg = state["status"]
        status_style = {
            "ok": f"fg:{FESTO_BLUE_CAERUL} bold",
            "warn": f"fg:{FESTO_SIGNAL_ORANGE} bold",
            "err": f"fg:{FESTO_SIGNAL_RED} bold",
            "info": f"fg:{FESTO_BLUE_2} bold",
        }.get(status_tone, f"fg:{FESTO_BLUE_2}")
        status_prefix = {
            "ok": "● OK",
            "warn": "▲ WARN",
            "err": "✗ ERROR",
            "info": "◇ INFO",
        }.get(status_tone, "◇ INFO")
        active = _active_depth()
        parts: list[tuple[str, str]] = [
            (f"fg:{FESTO_BLUE_CAERUL} bold", "\n  JOG MODE  "),
            (f"fg:{FESTO_GRAY_CANUL}", "(Esc or q to exit)\n\n"),
            (f"fg:{FESTO_BLUE_CAERUL} bold", "  Position:\n"),
        ]
        for axis, pos in loc.items():
            marker = " ◀" if axis == active and len(depth_axes) > 1 else ""
            parts += [
                ("", "    "),
                (f"fg:{FESTO_BLUE_2} bold", f"{axis:<6}"),
                (f"fg:{FESTO_GRAY_SUCANUL}", f"{pos:>10.3f} mm{marker}\n"),
            ]
        key_hints: list[str] = []
        if len(axis_names) >= 1:
            key_hints.append(f"  ←/→ {axis_names[0]}")
        if len(axis_names) >= 2:
            key_hints.append(f"  ↑/↓ {axis_names[1]}")
        if depth_axes:
            cycle_hint = " Tab cycle" if len(depth_axes) > 1 else ""
            key_hints.append(f"  PgUp/PgDn {active}{cycle_hint}")
        parts += [
            ("", "\n"),
            (f"fg:{FESTO_BLUE_CAERUL} bold", "  Step: "),
            (f"fg:{FESTO_BLUE_2} bold", f"{step} mm"),
            (f"fg:{FESTO_GRAY_CANUL}", "  (+ increase, - decrease)\n"),
            (f"fg:{FESTO_GRAY_CANUL}", "  Tab/Shift+Tab cycle depth axis\n\n"),
            (f"fg:{FESTO_GRAY_SUCANUL}", "    ".join(key_hints) + "\n\n"),
            (status_style, f"  {status_prefix}  {status_msg}\n"),
        ]
        return FormattedText(parts)

    kb = KeyBindings()
    app: Application = Application(
        layout=Layout(Window(content=FormattedTextControl(_content, focusable=True))),
        key_bindings=kb,
        full_screen=False,
    )

    def _do_jog(axis_name: str, direction: str) -> None:
        if state["busy"]:
            return
        step = _STEP_SIZES[state["step_idx"]]
        state["busy"] = True
        state["status"] = ("info", f"Moving {axis_name} {direction}{step:.3g} mm…")
        app.invalidate()

        def _run() -> None:
            try:
                state["location"] = session.jog(axis_name, direction, step)
                state["status"] = ("ok", f"{direction}{step:.3g} mm on {axis_name}")
            except (ValueError, KeyError) as exc:
                state["status"] = ("warn", f"Limit or config error: {exc}")
            except Exception as exc:
                logger.exception("CLI jog mode: jog failed axis=%s direction=%s step=%s", axis_name, direction, step)
                state["status"] = ("err", f"Error: {exc}")
            finally:
                state["busy"] = False
                app.invalidate()

        threading.Thread(target=_run, daemon=True).start()

    if len(axis_names) >= 1:

        @kb.add("left")
        def _(event) -> None:
            _do_jog(axis_names[0], "-")

        @kb.add("right")
        def _(event) -> None:
            _do_jog(axis_names[0], "+")

    if len(axis_names) >= 2:

        @kb.add("up")
        def _(event) -> None:
            _do_jog(axis_names[1], "+")

        @kb.add("down")
        def _(event) -> None:
            _do_jog(axis_names[1], "-")

    if depth_axes:

        @kb.add("pageup")
        def _(event) -> None:
            active = _active_depth()
            if active:
                _do_jog(active, "+")

        @kb.add("pagedown")
        def _(event) -> None:
            active = _active_depth()
            if active:
                _do_jog(active, "-")

        if len(depth_axes) > 1:

            @kb.add("tab")
            def _(event) -> None:
                state["depth_idx"] = (state["depth_idx"] + 1) % len(depth_axes)
                state["status"] = ("info", f"PgUp/PgDn now controls {depth_axes[state['depth_idx']]}")

            @kb.add("s-tab")
            def _(event) -> None:
                state["depth_idx"] = (state["depth_idx"] - 1) % len(depth_axes)
                state["status"] = ("info", f"PgUp/PgDn now controls {depth_axes[state['depth_idx']]}")

    @kb.add("+")
    def _(event) -> None:
        state["step_idx"] = min(state["step_idx"] + 1, len(_STEP_SIZES) - 1)
        state["status"] = ("ok", f"Step set to {_STEP_SIZES[state['step_idx']]} mm")

    @kb.add("-")
    def _(event) -> None:
        state["step_idx"] = max(state["step_idx"] - 1, 0)
        state["status"] = ("ok", f"Step set to {_STEP_SIZES[state['step_idx']]} mm")

    @kb.add("escape")
    @kb.add("q")
    def _(event) -> None:
        event.app.exit()

    try:
        app.run()
    except KeyboardInterrupt:
        pass
    console.print(f"{format_badge('JOG', 'info')} [festo.muted]Returned to REPL.[/]")


def run_repl(session: TeachSession, gantry: Gantry) -> int:  # noqa
    """Launch the interactive teach-in REPL for a connected gantry.

    Presents a prompt-toolkit REPL that accepts ``jog``, ``capture``,
    ``where``, ``home``, ``teach pos``, ``teach tray``, ``list``, ``save``,
    ``load``, ``help``, and ``quit`` commands.  Tab-completion and command
    history are provided automatically.

    Args:
        session: A [`TeachSession`][applied_motion.cli.session.TeachSession]
            instance backed by a connected, homed gantry.  Captured
            positions accumulate in ``session.positions``.
        gantry: The connected [`Gantry`][applied_motion.applied_motion.Gantry]
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
            raw = ps.prompt("motion> ").strip()
        except (EOFError, KeyboardInterrupt):  # noqa
            console.print("\n[festo.muted]Exiting.[/]")
            return 130

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                console.print("[festo.ok]✓[/] Quitting program.")
                break

            elif cmd == "help":
                console.print(_HELP_TEXT)

            elif cmd == "where":
                loc = gantry.get_location()
                console.print(_location_table(loc))

            elif cmd == "home":
                gantry.home()
                console.print("[festo.ok]✓[/] All axes homed.")

            elif cmd == "jog":
                if len(parts) == 1:
                    # No args → enter arrow-key jog TUI
                    _run_jog_mode(session, gantry)
                elif len(parts) < 4:
                    console.print(f"{format_badge('USAGE', 'warn')} [festo.brand]jog[/]")
                    console.print(f"    {format_help_line('jog', 'Enter arrow-key jog mode')}")
                    console.print(
                        f"    {format_help_line('jog <axis> <+/-> <step> [vel]', 'Single step-move (mm, default vel=10 mm/s)')}"
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
                        console.print(f"[festo.err]✗[/] {exc}")
                    except Exception as exc:
                        logger.exception(
                            "CLI command 'jog': failed axis=%s direction=%s step=%s vel=%s",
                            axis,
                            direction,
                            step,
                            vel,
                        )
                        console.print(f"[festo.err]✗[/] Move rejected by axis: {exc}")

            elif cmd == "capture":
                if len(parts) < 2:
                    console.print(f"[festo.err]✗[/] Usage: {format_help_line('capture <label>')}")
                    continue
                label = parts[1]
                session.capture(label)
                console.print(f"[festo.ok]✓[/] Captured [bold]{label!r}[/]")

            elif cmd == "teach":
                if not gantry.supports_teach():
                    console.print(
                        f"{format_badge('TEACH DISABLED', 'warn')} [festo.muted]Modbus backend has no PLC teach command.[/]\n"
                        "    Use [festo.ok]capture[/] to save positions to JSON instead."
                    )
                elif len(parts) >= 3 and parts[1] == "pos":
                    pos_id = int(parts[2])
                    gantry.teach_pos(pos_id=pos_id)
                    console.print(f"[festo.ok]✓[/] TEACH_POS sent ([festo.ok]pos_id={pos_id}[/])")
                elif len(parts) >= 4 and parts[1] == "tray":
                    tray_id, tray_pos = int(parts[2]), int(parts[3])
                    gantry.teach_tray(tray_id=tray_id, tray_pos=tray_pos)
                    console.print(
                        f"[festo.ok]✓[/] TEACH_TRAY sent ([festo.ok]tray_id={tray_id}[/], [festo.ok]tray_pos={tray_pos}[/])"
                    )
                else:
                    console.print("[festo.err]✗[/] Usage:")
                    console.print(f"    {format_help_line('teach pos <pos_id>')}")
                    console.print(f"    {format_help_line('teach tray <tray_id> <tray_pos>')}")

            elif cmd == "list":
                if not session.positions:
                    console.print("[festo.muted]No positions captured yet.[/]")
                else:
                    console.print(_positions_table(session.positions))

            elif cmd == "save":
                if len(parts) < 2:
                    console.print(f"[festo.err]✗[/] Usage: {format_help_line('save <path>')}")
                    continue
                session.save(parts[1])
                console.print(f"[festo.ok]✓[/] {len(session.positions)} position(s) saved → [bold]{parts[1]}[/]")

            elif cmd == "load":
                if len(parts) < 2:
                    console.print(f"[festo.err]✗[/] Usage: {format_help_line('load <path>')}")
                    continue
                before = len(session.positions)
                session.load(parts[1])
                added = len(session.positions) - before
                console.print(f"[festo.ok]✓[/] {added} position(s) loaded from [bold]{parts[1]}[/]")

            else:
                console.print(f"[festo.err]✗[/] Unknown command: [bold]{cmd!r}[/]  (type [festo.ok]help[/])")

        except (KeyError, ValueError, IndexError) as exc:
            console.print(f"[festo.err]✗[/] {exc}")
        except Exception as exc:
            logger.exception("Unexpected error processing command %r", raw)
            console.print(f"[festo.err]✗[/] Unexpected error: {exc}")
    console.print("[festo.ok]✓[/] Quitting program repl successful.")
    return 1


def _configure_logging(log_level: str) -> None:
    """Configure process logging for CLI execution.

    Args:
        log_level: Logging threshold name, such as ``"INFO"`` or
            ``"WARNING"``.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _connect_gantry(config_path: Path, gantry_name: str) -> Gantry:
    """Build a gantry instance from configuration.

    Args:
        config_path: Path to gantry configuration JSON.
        gantry_name: Gantry component name in configuration.

    Returns:
        Connected gantry instance.
    """
    return Gantry.from_config(config_path, name=gantry_name)


def _run_shell(args: argparse.Namespace, gantry: Gantry) -> int:
    """Run interactive teach shell command.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    console.print(f"{format_badge('CONNECTED', 'ok')} [bold]{gantry!r}[/]")

    on_capture = None
    if gantry.supports_teach():

        def on_capture(label: str, pos: dict[str, float]) -> None:
            console.print(
                f"  {format_badge('TIP', 'info')} [festo.muted]run [festo.ok]teach pos <id>[/] to commit [bold]{label!r}[/] to PLC.[/]"
            )

    session = TeachSession(gantry, on_capture=on_capture)
    exit_code = run_repl(session, gantry)
    console.print("[festo.ok]✓[/] Program shell exited successfully.")
    return exit_code


def _run_where(args: argparse.Namespace, gantry: Gantry) -> int:
    """Print axis positions once.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    console.print(_location_table(gantry.get_location()))
    return 0


def _run_home(args: argparse.Namespace, gantry: Gantry) -> int:
    """Home all axes once.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    gantry.home()
    console.print("[festo.ok]✓[/] All axes homed.")
    return 0


def _run_status(args: argparse.Namespace, gantry: Gantry) -> int:
    """Print gantry status snapshot.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    status = gantry.get_status()
    if args.as_json:
        console.print_json(data=status)
    else:
        console.print(f"[festo.brand]Gantry status[/] [festo.muted]({status.get('backend', 'unknown')})[/]")

        summary = status.get("summary", {}) if isinstance(status, dict) else {}
        summary_table = Table(
            show_header=True,
            header_style="festo.brand",
            box=box.SIMPLE_HEAD,
            border_style=FESTO_BLUE_2,
            padding=(0, 1),
            row_styles=["", "festo.muted"],
        )
        summary_table.add_column("Summary", style="festo.ok")
        summary_table.add_column("Value", style="festo.value")
        healthy = summary.get("healthy")
        healthy_badge = format_badge("HEALTHY", "ok") if healthy is True else format_badge("ATTENTION", "err")
        summary_table.add_row("health", healthy_badge)
        summary_table.add_row("supports teach", format_bool(status.get("supports_teach")))
        summary_table.add_row("all homed", format_bool(summary.get("all_homed")))
        summary_table.add_row("all stopped", format_bool(summary.get("all_stopped")))
        summary_table.add_row("all ready", format_bool(summary.get("all_ready_for_motion")))
        summary_table.add_row("axis count", str(summary.get("axis_count", "n/a")))
        console.print(summary_table)

        axes = status.get("axes", {}) if isinstance(status, dict) else {}
        if isinstance(axes, dict) and axes:
            axis_table = Table(
                show_header=True,
                header_style="festo.brand",
                box=box.SIMPLE_HEAD,
                border_style=FESTO_BLUE_2,
                padding=(0, 1),
                row_styles=["", "festo.muted"],
            )
            axis_table.add_column("Axis", style="festo.ok")
            axis_table.add_column("Position (mm)", justify="right", style="festo.value")
            axis_table.add_column("Homed", style="festo.value")
            axis_table.add_column("Stopped", style="festo.value")
            axis_table.add_column("Ready", style="festo.value")
            axis_table.add_column("Error", style="festo.value")
            for axis_name, axis_state in axes.items():
                if not isinstance(axis_state, dict):
                    continue
                pos = axis_state.get("position_mm")
                pos_text = "n/a" if pos is None else f"{float(pos):.3f}"
                error_text = axis_state.get("error")
                error_cell = "[festo.muted]—[/]" if not error_text else f"[festo.err]{error_text}[/]"
                axis_table.add_row(
                    str(axis_name),
                    pos_text,
                    format_bool(axis_state.get("is_homed")),
                    format_bool(axis_state.get("is_stopped")),
                    format_bool(axis_state.get("ready_for_motion")),
                    error_cell,
                )
            console.print(axis_table)

        controller = status.get("controller", {}) if isinstance(status, dict) else {}
        if isinstance(controller, dict) and any(value is not None for value in controller.values()):
            controller_table = Table(
                show_header=True,
                header_style="festo.brand",
                box=box.SIMPLE_HEAD,
                border_style=FESTO_BLUE_2,
                padding=(0, 1),
                row_styles=["", "festo.muted"],
            )
            controller_table.add_column("Controller", style="festo.ok")
            controller_table.add_column("Value", style="festo.value")
            controller_table.add_row("sys status", str(controller.get("sys_status") or "n/a"))
            controller_table.add_row("is error", format_bool(controller.get("is_error")))
            controller_table.add_row("fpb error", str(controller.get("fpb_error") or "n/a"))
            controller_table.add_row("read error", str(controller.get("read_err") or "n/a"))
            controller_error = controller.get("error")
            controller_table.add_row(
                "error", "[festo.muted]—[/]" if controller_error is None else f"[festo.err]{controller_error}[/]"
            )
            console.print(controller_table)
    return 0


def _run_jog(args: argparse.Namespace, gantry: Gantry) -> int:
    """Execute one jog command in non-interactive mode.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    session = TeachSession(gantry)
    location = session.jog(args.axis.upper(), args.direction, args.step, args.velocity, timeout=args.timeout)
    console.print(_location_table(location))
    return 0


def _run_teach_pos(args: argparse.Namespace, gantry: Gantry) -> int:
    """Execute PLC TEACH_POS command.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    if not gantry.supports_teach():
        raise NotImplementedError("Configured backend does not support TEACH_POS")
    gantry.teach_pos(pos_id=args.pos_id)
    console.print(f"[festo.ok]✓[/] TEACH_POS sent ([festo.ok]pos_id={args.pos_id}[/])")
    return 0


def _run_teach_tray(args: argparse.Namespace, gantry: Gantry) -> int:
    """Execute PLC TEACH_TRAY command.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    if not gantry.supports_teach():
        raise NotImplementedError("Configured backend does not support TEACH_TRAY")
    gantry.teach_tray(tray_id=args.tray_id, tray_pos=args.tray_pos)
    console.print(
        f"[festo.ok]✓[/] TEACH_TRAY sent ([festo.ok]tray_id={args.tray_id}[/], [festo.ok]tray_pos={args.tray_pos}[/])"
    )
    return 0


def _run_jog_tui(args: argparse.Namespace, gantry: Gantry) -> int:
    """Launch arrow-key jog TUI directly without entering the teach REPL.

    Connects to the gantry, then starts the interactive jog mode where
    arrow keys step axes and Esc or q exits.

    Args:
        args: Parsed CLI arguments.
        gantry: Connected gantry instance.

    Returns:
        Process exit code.
    """
    console.print(f"{format_badge('CONNECTED', 'ok')} [bold]{gantry!r}[/]")
    session = TeachSession(gantry)
    _run_jog_mode(session, gantry)
    return 0


def _add_motion_command_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register built-in motion command parsers.

    Args:
        subparsers: Subparser action used for command registration.
    """
    shell_parser = subparsers.add_parser("shell", help="Run interactive teach shell")
    shell_parser.set_defaults(_handler=_run_shell)

    where_parser = subparsers.add_parser("where", help="Print current axis positions")
    where_parser.set_defaults(_handler=_run_where)

    home_parser = subparsers.add_parser("home", help="Home all axes")
    home_parser.set_defaults(_handler=_run_home)

    status_parser = subparsers.add_parser("status", help="Print gantry status")
    status_parser.add_argument("--json", dest="as_json", action="store_true", help="Print status as JSON")
    status_parser.set_defaults(_handler=_run_status)

    jog_parser = subparsers.add_parser("jog", help="Run one non-interactive jog step")
    jog_parser.add_argument("axis", help="Axis name")
    jog_parser.add_argument("direction", choices=_DIRECTIONS, help="Direction: '+' or '-'")
    jog_parser.add_argument("step", type=float, help="Jog distance in mm")
    jog_parser.add_argument("--velocity", type=float, default=10.0, help="Jog speed in mm/s")
    jog_parser.add_argument("--timeout", type=int, default=30, help="Move timeout in seconds")
    jog_parser.set_defaults(_handler=_run_jog)

    teach_pos_parser = subparsers.add_parser("teach-pos", help="Send backend TEACH_POS")
    teach_pos_parser.add_argument("pos_id", type=int, help="PLC position slot ID")
    teach_pos_parser.set_defaults(_handler=_run_teach_pos)

    teach_tray_parser = subparsers.add_parser("teach-tray", help="Send backend TEACH_TRAY")
    teach_tray_parser.add_argument("tray_id", type=int, help="PLC tray ID")
    teach_tray_parser.add_argument("tray_pos", type=int, help="PLC tray position index")
    teach_tray_parser.set_defaults(_handler=_run_teach_tray)

    jog_tui_parser = subparsers.add_parser("jog-tui", help="Arrow-key interactive jog TUI")
    jog_tui_parser.set_defaults(_handler=_run_jog_tui)


def register_motion_cli(
    parent_subparsers: argparse._SubParsersAction,
    *,
    command_name: str = "motion",
    extensions: Sequence[MotionCliExtension] = (),
) -> argparse.ArgumentParser:
    """Attach motion CLI subtree to parent parser.

    Designed for higher-level system CLIs that compose multiple domains
    (for example ``motion`` and ``fluid``) side-by-side.

    Args:
        parent_subparsers: Parent parser subcommand registry.
        command_name: Name used for mounted motion command subtree.
        extensions: Optional extension hooks that can add extra motion
            subcommands.

    Returns:
        Mounted motion command parser.
    """
    parser = parent_subparsers.add_parser(command_name, help="Motion control commands")
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to gantry JSON configuration file.",
    )
    parser.add_argument(
        "--gantry-name",
        default="gantry_1",
        metavar="NAME",
        help="Gantry component name in config (default: gantry_1).",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        metavar="LEVEL",
        help="Python logging level (default: WARNING).",
    )
    motion_subparsers = parser.add_subparsers(dest="motion_command")
    _add_motion_command_parsers(motion_subparsers)
    for extension in extensions:
        extension(motion_subparsers)
    return parser


def build_standalone_motion_parser(
    *,
    prog: str = "applied-motion",
    extensions: Sequence[MotionCliExtension] = (),
) -> argparse.ArgumentParser:
    """Build standalone motion CLI parser.

    Args:
        prog: Program name shown in help output.
        extensions: Optional extension hooks for additional subcommands.

    Returns:
        Fully configured standalone parser.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Composable motion control CLI for teach-in and axis operations.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to gantry JSON configuration file.",
    )
    parser.add_argument(
        "--gantry-name",
        default="gantry_1",
        metavar="NAME",
        help="Gantry component name in config (default: gantry_1).",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        metavar="LEVEL",
        help="Python logging level (default: WARNING).",
    )
    subparsers = parser.add_subparsers(dest="motion_command")
    _add_motion_command_parsers(subparsers)
    for extension in extensions:
        extension(subparsers)
    return parser


def dispatch_motion_command(args: argparse.Namespace) -> int:
    """Dispatch parsed motion command namespace.

    Initialises the gantry once and passes it to the selected handler.
    Guarantees ``gantry.close()`` is called on exit regardless of outcome.

    Args:
        args: Parsed argument namespace from a motion parser.

    Returns:
        Process exit code.

    Raises:
        ValueError: If no command handler is available in *args*.
    """
    if hasattr(args, "log_level"):
        _configure_logging(args.log_level)

    handler = getattr(args, "_handler", None)
    if handler is None:
        raise ValueError("No motion command selected")
    gantry = _connect_gantry(args.config, args.gantry_name)
    try:
        return handler(args, gantry)
    finally:
        gantry.close()


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``applied-motion`` standalone CLI command.

    Args:
        argv: Optional argv override used by tests.
    """
    parser = build_standalone_motion_parser()
    args = parser.parse_args(argv)
    if args.motion_command is None:
        args._handler = _run_shell
    exit_code = 1
    try:
        console.print(f"[festo.muted]Loading config:[/] {args.config}")
        exit_code = dispatch_motion_command(args)
    except KeyboardInterrupt:
        console.print("\n[festo.muted]Interrupted.[/]")
        sys.exit(130)
    except Exception as exc:
        logger.debug("CLI fatal error", exc_info=True)
        console.print(f"[festo.err]✗[/] {exc}")
        sys.exit(1)

    if exit_code:
        tone = "warn" if exit_code == 130 else "err"
        console.print(f"{format_badge('EXIT', tone)} [festo.muted]code {exit_code}[/]")
        console.print("Ctrl+c to end shell")
        sys.exit(exit_code)


# TODO: Add hook to cli to enable/disable gantry for manual motion/teach in
if __name__ == "__main__":
    main()
