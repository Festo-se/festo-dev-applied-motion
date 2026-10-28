# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Validation modes for shared configuration parsing."""

from enum import Enum


class ValidationMode(Enum):
    """Select compatibility or strict validation behavior."""

    COMPAT = "compat"
    STRICT = "strict"
