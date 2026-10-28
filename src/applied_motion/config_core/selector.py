# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Compatibility delegate for canonical component selection."""

from typing import Any, cast

from configurator import Configurator, SelectionError


class ComponentSelector:
    """Select named component from normalized configuration."""

    @staticmethod
    def select(normalized: dict[str, Any], selector: str) -> dict[str, Any]:
        """Select component by mapping key.

        Args:
            normalized: Normalized configuration mapping.
            selector: Component key.

        Returns:
            Selected component mapping.

        Raises:
            ValueError: If components or selected component is invalid.
        """
        components = normalized.get("components")
        if not isinstance(components, dict):
            raise ValueError("Config config must contain a 'components' mapping")
        try:
            matches = Configurator._select(components, selector)
        except SelectionError as exc:
            if selector not in components:
                raise ValueError(f"Config config does not contain component {selector!r}") from exc
            raise
        component = cast(dict[str, Any], matches[0][1])
        if not isinstance(component, dict):
            raise ValueError(f"Config component {selector!r} must be a dict")
        return component
