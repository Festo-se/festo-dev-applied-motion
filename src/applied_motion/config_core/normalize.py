# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Compatibility delegate for canonical configurator normalization."""

from typing import Any

from configurator import SourceError, normalize_root


class RootConfigNormalizer:
    """Unwrap one optional outer ``component_config`` mapping."""

    @staticmethod
    def normalize(raw: dict[str, Any]) -> dict[str, Any]:
        """Return normalized configuration mapping.

        Args:
            raw: Raw configuration mapping.

        Returns:
            Mapping after one-level wrapper removal.

        Raises:
            ValueError: If normalized value is not a mapping.
        """
        try:
            return normalize_root(raw)
        except SourceError as exc:
            raise ValueError("Normalized config config must be a dict") from exc
