# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Typed results shared by configuration consumers."""

from dataclasses import dataclass
from typing import Any

from .diagnostics import ValidationIssue


@dataclass(frozen=True)
class ValidationResult:
    """Return parsed component data and strict diagnostics."""

    data: dict[str, Any]
    diagnostics: tuple[ValidationIssue, ...] = ()
