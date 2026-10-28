# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Dependency-free shared configuration core."""

from .diagnostics import ValidationIssue
from .mode import ValidationMode
from .normalize import RootConfigNormalizer
from .parser import parse_component
from .source import ConfigSourceLoader
from .types import ValidationResult
from .validators.gantry import validate_gantry_component

__all__ = [
    "ConfigSourceLoader",
    "RootConfigNormalizer",
    "ValidationIssue",
    "ValidationMode",
    "ValidationResult",
    "parse_component",
    "validate_gantry_component",
]
