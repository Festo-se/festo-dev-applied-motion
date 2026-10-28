# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Compatibility delegate for canonical configurator source loading."""

from pathlib import Path
from typing import Any

from configurator import SourceError, load_source


class ConfigSourceLoader:
    """Load mapping configuration from dictionaries or JSON paths."""

    @staticmethod
    def load(source: dict[str, Any] | Path | str) -> dict[str, Any]:
        """Load configuration mapping.

        Args:
            source: Mapping or JSON file path.

        Returns:
            Loaded mapping.

        Raises:
            ValueError: If loaded value is not a mapping.
        """
        if isinstance(source, dict):
            return source
        try:
            value, _ = load_source(source)
        except SourceError as exc:
            raise ValueError("Config config must load as a dict") from exc
        return value
