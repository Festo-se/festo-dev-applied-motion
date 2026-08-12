# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Reusable formatting helpers for Festo-styled CLI output.

This module centralises Rich-markup snippets used by command help and usage
messages so style choices stay consistent across multiple REPL entry points.
"""

from collections.abc import Sequence


def format_usage(usage: str) -> str:
    """Return a usage string with Festo style tags applied token-by-token.

    Args:
        usage: Plain usage text such as ``"jog <axis> <+/-> <step> [vel]"``.

    Returns:
        Rich-markup usage string where command tokens use ``festo.ok``, required
        arguments use ``festo.value``, and optional arguments use ``festo.muted``.
    """
    styled_tokens: list[str] = []
    for token in usage.split():
        clean = token[1:] if token.startswith("\\") else token
        if clean.startswith("<") and clean.endswith(">"):
            styled_tokens.append(f"[festo.value]{token}[/]")
        elif clean.startswith("[") and clean.endswith("]"):
            styled_tokens.append(f"[festo.muted]{token}[/]")
        else:
            styled_tokens.append(f"[festo.ok]{token}[/]")
    return " ".join(styled_tokens)


def format_help_line(usage: str, description: str = "", indent: int = 0) -> str:
    """Return a single styled help line.

    Args:
        usage: Command usage text.
        description: Optional description shown to the right of usage.
        indent: Two-space indentation depth.

    Returns:
        Styled single-line help entry.
    """
    pad = "  " * indent
    rendered_usage = format_usage(usage)
    if description:
        return f"{pad}{rendered_usage}    [festo.muted]{description}[/]"
    return f"{pad}{rendered_usage}"


def format_group_header(name: str, description: str = "", indent: int = 0) -> str:
    """Return a styled namespace/group header line.

    Args:
        name: Group name (namespace token).
        description: Optional group description.
        indent: Two-space indentation depth.

    Returns:
        Styled single-line group header.
    """
    pad = "  " * indent
    rendered_name = f"[festo.brand]{name}[/]"
    if description:
        return f"{pad}{rendered_name}    [festo.muted]{description}[/]"
    return f"{pad}{rendered_name}"


def format_badge(label: str, tone: str = "info") -> str:
    """Return a compact styled badge label.

    Args:
        label: Badge text to render.
        tone: Badge tone name. Supported values are ``"ok"``, ``"warn"``,
            ``"err"``, ``"muted"``, and ``"info"``.

    Returns:
        Rich-markup badge string.
    """
    style_map = {
        "ok": "festo.ok",
        "warn": "festo.warn",
        "err": "festo.err",
        "muted": "festo.muted",
        "info": "festo.info",
    }
    style = style_map.get(tone, "festo.info")
    return f"[{style}]● {label}[/]"


def format_bool(value: bool | None) -> str:
    """Return styled yes/no/unknown marker for booleans.

    Args:
        value: Boolean-like value.

    Returns:
        Styled marker string.
    """
    if value is True:
        return format_badge("YES", "ok")
    if value is False:
        return format_badge("NO", "err")
    return format_badge("N/A", "muted")


def build_help_block(
    entries: Sequence[tuple[str, str]],
    *,
    title: str = "Commands",
    footer: Sequence[str] = (),
) -> str:
    """Build a complete styled help block from usage-description pairs.

    Args:
        entries: Ordered ``(usage, description)`` pairs.
        title: Section heading.
        footer: Additional already-styled lines to append.

    Returns:
        Newline-joined Rich-markup block.
    """
    lines = [f"[festo.brand]{title}[/]"]
    lines.extend(format_help_line(usage, description) for usage, description in entries)
    lines.extend(footer)
    return "\n".join(lines)


__all__ = [
    "build_help_block",
    "format_badge",
    "format_bool",
    "format_group_header",
    "format_help_line",
    "format_usage",
]
