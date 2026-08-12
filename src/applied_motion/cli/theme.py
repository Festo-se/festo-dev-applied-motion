# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Festo-branded Rich theme and console factory.

Palette sourced from the Festo corporate design system (``docs/css/extra.css``
across all Festo Python packages).  Proportions follow the Festo corporate
colour-proportion guideline (dominant white space → black text → grey tones →
light blue highlight → Festo blue call-to-action accent):

=========================  =========  ==============================================
Name                       Hex        Reference
=========================  =========  ==============================================
``FESTO_BLACK_ATERUL``     #000000    Font / body text
``FESTO_CHARCOAL``         #333333    Dark backgrounds, strong text
``FESTO_GRAY_1``           #717D86    Pantone 431 C — darkest grey
``FESTO_GRAY_2``           #949EA6    Pantone 430 C
``FESTO_GRAY_CANUL``       #B6BEC6    Pantone 429 C — borders, muted labels
``FESTO_GRAY_4``           #D0D6DC    Pantone 428 C
``FESTO_GRAY_SUCANUL``     #E5E8EB    Pantone 427 C — large areas, table values
``FESTO_BLUE_CAERUL``      #0091DC    HKS 47 K / PMS Process Blue — call-to-action
``FESTO_BLUE_2``           #48B9EB    Pantone 298 C
``FESTO_BLUE_3``           #92D5F6    Pantone 297 C
``FESTO_BLUE_SUCAERUL``    #C8E6FA    Pantone 290 C — highlight / subtle accents
``FESTO_BLUE_5``           #DEF0FC    Lightest blue tint
=========================  =========  ==============================================

Import this module in any CLI that needs Festo-consistent output::

    from applied_motion.cli.theme import festo_console, FESTO_BLUE_CAERUL

    console = festo_console()
    console.print("[festo.brand]Motion Control CLI[/]")
    console.print("[festo.ok]✓[/] Axes homed.")

Named styles
------------
``festo.brand``
    Bold Festo blue — section headers, product names.
``festo.ok``
    Festo blue — success marks, command names in help text, inline values.
``festo.muted``
    Mid-grey — secondary labels, dimmed hints.
``festo.value``
    Light grey — numeric values in tables.
"""

from rich.console import Console
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

FESTO_WHITE: str = "#FFFFFF"
FESTO_BLACK_ATERUL: str = "#000000"
FESTO_CHARCOAL: str = "#333333"
FESTO_TEXT_LIGHT: str = "#82868B"  # --fwe-text-light
# Grey scale — light to dark
FESTO_GRAY_1: str = "#717D86"  # Pantone 431 C
FESTO_GRAY_2: str = "#949EA6"  # Pantone 430 C
FESTO_GRAY_CANUL: str = "#B6BEC6"  # Pantone 429 C
FESTO_GRAY_4: str = "#D0D6DC"  # Pantone 428 C
FESTO_GRAY_SUCANUL: str = "#E5E8EB"  # Pantone 427 C
# Blue scale — saturated to tint
FESTO_BLUE_6: str = "#003049"  # darkest navy
FESTO_BLUE_7: str = "#006193"  # dark navy
FESTO_BLUE_CAERUL: str = "#0091DC"  # HKS 47 K / PMS Process Blue
FESTO_BLUE_2: str = "#48B9EB"  # Pantone 298 C
FESTO_BLUE_3: str = "#92D5F6"  # Pantone 297 C
FESTO_BLUE_SUCAERUL: str = "#C8E6FA"  # Pantone 290 C
FESTO_BLUE_5: str = "#DEF0FC"
# Layout greys
FESTO_LAYOUT_1: str = "#F0F2F3"
FESTO_LAYOUT_3: str = "#D3D8DD"
# Interaction colours
FESTO_INTERACTION_HOVER: str = "#0588CB"  # blue mouse-over
FESTO_INTERACTION_PRESSED: str = "#0A7EBA"  # blue mouse-pressed
FESTO_DISABLED_FIELD: str = "#E2E5E8"  # disabled field background
FESTO_DISABLED_TEXT: str = "#A9B0B7"  # disabled text / grey mouse-pressed
FESTO_INTERACTION_GRAY_HOVER: str = "#C5CBD1"  # grey mouse-over
FESTO_CONTROL: str = "#DBDFE3"  # --fwe-control base
# Icon / hero colours
FESTO_ICON_GRAY: str = "#A3B2BC"
# Signal colours — use sparingly (~5% of UI per Festo guidelines)
FESTO_SIGNAL_GREEN: str = "#80CA3D"
FESTO_SIGNAL_YELLOW: str = "#FFD600"
FESTO_SIGNAL_ORANGE: str = "#FF9600"  # warning base
FESTO_SIGNAL_WARNING_HOVER: str = "#EA8C05"  # warning hover
FESTO_SIGNAL_WARNING_PRESSED: str = "#D6820A"  # warning pressed
FESTO_SIGNAL_RED: str = "#D50000"  # error base
FESTO_SIGNAL_ERROR_HOVER: str = "#C40505"  # error hover
FESTO_SIGNAL_ERROR_PRESSED: str = "#B50A0A"  # error pressed
# Shadow — #333333 at 20% opacity; X=0, Y=1, spread=4 or 8
FESTO_SHADOW: str = "#33333333"  # RRGGBBAA, 0x33 = 20% of 0xFF
# State colouring (BG / icon; normal icon = FESTO_CHARCOAL, transparent BG = no constant)
FESTO_STATE_HOVER_BG: str = "#D8DCE1"
FESTO_STATE_PRESSED_BG: str = "#C7CBCF"
FESTO_STATE_ACTIVE_BG: str = "#B7BABE"
FESTO_STATE_DISABLED_ICON: str = "#B9BABB"
FESTO_STATE_DISABLED_CHECKED_BG: str = "#FBFBFB"

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

FESTO_THEME = Theme(
    {
        # Brand header — bold Festo blue
        "festo.brand": f"bold {FESTO_BLUE_CAERUL}",
        # Success / highlight — Festo blue (replaces generic green)
        "festo.ok": FESTO_BLUE_CAERUL,
        # Informational accents
        "festo.info": FESTO_BLUE_2,
        # Muted secondary text
        "festo.muted": FESTO_GRAY_CANUL,
        # Table / data values
        "festo.value": FESTO_GRAY_SUCANUL,
        # Signals
        "festo.warn": f"bold {FESTO_SIGNAL_ORANGE}",
        "festo.err": f"bold {FESTO_SIGNAL_RED}",
    }
)


def festo_console() -> Console:
    """Return a Rich :class:`~rich.console.Console` using the Festo brand theme.

    All :func:`~rich.console.Console.print` calls on the returned console
    recognise ``[festo.brand]``, ``[festo.ok]``, ``[festo.muted]``, and
    ``[festo.value]`` as styled markup tags in addition to standard Rich tags.

    Returns:
        Themed console instance ready for CLI output.
    """
    return Console(theme=FESTO_THEME)


__all__ = [
    "FESTO_WHITE",
    "FESTO_BLACK_ATERUL",
    "FESTO_CHARCOAL",
    "FESTO_TEXT_LIGHT",
    "FESTO_GRAY_1",
    "FESTO_GRAY_2",
    "FESTO_GRAY_CANUL",
    "FESTO_GRAY_4",
    "FESTO_GRAY_SUCANUL",
    "FESTO_BLUE_6",
    "FESTO_BLUE_7",
    "FESTO_BLUE_CAERUL",
    "FESTO_BLUE_2",
    "FESTO_BLUE_3",
    "FESTO_BLUE_SUCAERUL",
    "FESTO_BLUE_5",
    "FESTO_LAYOUT_1",
    "FESTO_LAYOUT_3",
    "FESTO_INTERACTION_HOVER",
    "FESTO_INTERACTION_PRESSED",
    "FESTO_DISABLED_FIELD",
    "FESTO_DISABLED_TEXT",
    "FESTO_INTERACTION_GRAY_HOVER",
    "FESTO_CONTROL",
    "FESTO_ICON_GRAY",
    "FESTO_SIGNAL_GREEN",
    "FESTO_SIGNAL_YELLOW",
    "FESTO_SIGNAL_ORANGE",
    "FESTO_SIGNAL_WARNING_HOVER",
    "FESTO_SIGNAL_WARNING_PRESSED",
    "FESTO_SIGNAL_RED",
    "FESTO_SIGNAL_ERROR_HOVER",
    "FESTO_SIGNAL_ERROR_PRESSED",
    "FESTO_SHADOW",
    "FESTO_STATE_HOVER_BG",
    "FESTO_STATE_PRESSED_BG",
    "FESTO_STATE_ACTIVE_BG",
    "FESTO_STATE_DISABLED_ICON",
    "FESTO_STATE_DISABLED_CHECKED_BG",
    "FESTO_THEME",
    "festo_console",
]
