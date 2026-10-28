# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Reusable dependency-free configuration validators."""

from typing import Any


def require_mapping(value: Any, message: str) -> dict[str, Any]:
    """Return mapping or raise compatibility error.

    Args:
        value: Value to validate.
        message: Error message.

    Returns:
        Validated mapping.

    Raises:
        ValueError: If value is not a mapping.
    """
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def require_string_list(value: Any, field_name: str) -> list[str]:
    """Return list of strings.

    Args:
        value: Value to validate.
        field_name: Field name for compatibility error.

    Returns:
        Validated string list.

    Raises:
        ValueError: If value is not a list of strings.
    """
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Config {field_name} must be a list of axis-name strings")
    return value


def unknown_items(items: list[str], allowed: dict[str, Any]) -> list[str]:
    """Return items absent from allowed mapping.

    Args:
        items: Candidate names.
        allowed: Allowed-name mapping.

    Returns:
        Unknown names in input order.
    """
    return [item for item in items if item not in allowed]
