# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Generic configuration parsing pipeline."""

from typing import Any

from .mode import ValidationMode
from .normalize import RootConfigNormalizer
from .selector import ComponentSelector
from .source import ConfigSourceLoader
from .types import ValidationResult
from .validators.gantry import validate_gantry_component


def parse_component(
    source: dict[str, Any] | str,
    selector: str,
    validation_mode: ValidationMode = ValidationMode.COMPAT,
) -> ValidationResult:
    """Load, normalize, and select one component.

    Args:
        source: Configuration mapping or JSON path.
        selector: Component key.
        validation_mode: Compatibility or strict mode. Core structural
            validation is added by domain validators.

    Returns:
        Selected component and diagnostics.

    Raises:
        ValueError: If source, root, or component is invalid.
    """
    normalized = RootConfigNormalizer.normalize(ConfigSourceLoader.load(source))
    component = ComponentSelector.select(normalized, selector)
    if component.get("component_class") == "gantry" or "axes" in component:
        validate_gantry_component(component, selector)
    del validation_mode
    return ValidationResult(data=component)
