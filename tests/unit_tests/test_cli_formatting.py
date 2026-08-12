"""Unit tests for modular CLI Rich-markup formatting helpers."""

from applied_motion.cli.compose.core import Command, CommandGroup
from applied_motion.cli.compose.repl import render_help
from applied_motion.cli.formatting import (
    build_help_block,
    format_badge,
    format_bool,
    format_group_header,
    format_help_line,
    format_usage,
)


def test_format_usage_styles_command_and_args() -> None:
    rendered = format_usage("jog <axis> <+/-> <step> [vel]")

    assert "[festo.ok]jog[/]" in rendered
    assert "[festo.value]<axis>[/]" in rendered
    assert "[festo.value]<step>[/]" in rendered
    assert "[festo.muted][vel][/]" in rendered


def test_format_help_line_includes_description_style() -> None:
    rendered = format_help_line("home", "Home all axes", indent=1)

    assert rendered.startswith("  [festo.ok]home[/]")
    assert "[festo.muted]Home all axes[/]" in rendered


def test_format_group_header_styles_namespace() -> None:
    rendered = format_group_header("gantry", "Mount-arm controls", indent=1)

    assert rendered.startswith("  [festo.brand]gantry[/]")
    assert "[festo.muted]Mount-arm controls[/]" in rendered


def test_build_help_block_renders_title_entries_and_footer() -> None:
    rendered = build_help_block(
        [("where", "Print current axis positions")],
        footer=("[festo.ok]quit[/]",),
    )

    assert rendered.splitlines()[0] == "[festo.brand]Commands[/]"
    assert "[festo.ok]where[/]" in rendered
    assert rendered.splitlines()[-1] == "[festo.ok]quit[/]"


def test_format_badge_uses_requested_tone() -> None:
    rendered = format_badge("READY", "ok")
    assert rendered == "[festo.ok]● READY[/]"


def test_format_badge_defaults_to_info_tone_for_unknown_value() -> None:
    rendered = format_badge("STATE", "unknown")
    assert rendered == "[festo.info]● STATE[/]"


def test_format_bool_true_false_none() -> None:
    assert format_bool(True) == "[festo.ok]● YES[/]"
    assert format_bool(False) == "[festo.err]● NO[/]"
    assert format_bool(None) == "[festo.muted]● N/A[/]"


def test_render_help_uses_modular_styling() -> None:
    child = CommandGroup("gantry", help="Axis control")
    child.add_command(Command("home", lambda _: None, usage="home", help="Home axes"))

    root = CommandGroup("motion")
    root.add_command(Command("where", lambda _: None, usage="where", help="Print location"))
    root.add_child(child)

    rendered = render_help(root)

    assert "[festo.brand]Commands[/]" in rendered
    assert "[festo.ok]where[/]" in rendered
    assert "[festo.brand]gantry[/]" in rendered
