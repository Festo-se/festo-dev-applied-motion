# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Structured validation diagnostics."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    """Describe one validation finding."""

    path: str
    code: str
    message: str
    severity: str = "error"
    expected_type: str | None = None
    actual_type: str | None = None
