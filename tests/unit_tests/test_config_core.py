# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Contract tests for dependency-free shared config core."""

import json
from unittest import TestCase

import pytest

from applied_motion.config_core import (
    ConfigSourceLoader,
    RootConfigNormalizer,
    ValidationMode,
    parse_component,
)


def test_loads_mapping_and_path(tmp_path):
    """Load mappings from direct, Path, and string-path sources."""
    config = {"components": {"test": {"component_class": "test"}}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    case = TestCase()
    case.assertIs(ConfigSourceLoader.load(config), config)
    case.assertEqual(ConfigSourceLoader.load(path), config)
    case.assertEqual(ConfigSourceLoader.load(str(path)), config)


def test_normalizes_one_outer_wrapper():
    """Remove only one outer component-config wrapper."""
    wrapped = {"component_config": {"components": {"test": {}}}}

    TestCase().assertEqual(RootConfigNormalizer.normalize(wrapped), wrapped["component_config"])


def test_rejects_non_mapping_source():
    """Reject non-mapping source values with compatibility message."""
    with pytest.raises(ValueError, match="Config config must load as a dict"):
        ConfigSourceLoader.load([])


def test_selects_component_and_preserves_mapping():
    """Return selected component without copying its mapping."""
    component = {"component_class": "test"}
    config = {"components": {"test": component}}

    result = parse_component(config, "test", ValidationMode.STRICT)

    case = TestCase()
    case.assertIs(result.data, component)
    case.assertEqual(result.diagnostics, ())


def test_validates_gantry_component():
    """Validate Modbus axes through generic parser."""
    config = {
        "components": {
            "gantry_1": {
                "component_class": "gantry",
                "axes": {"x": {"name": "x", "ip": "192.168.1.10"}},
            }
        }
    }

    result = parse_component(config, "gantry_1")

    TestCase().assertEqual(result.data["axes"]["x"]["name"], "x")


def test_rejects_invalid_gantry_axis():
    """Reject Modbus axis missing required IP field."""
    config = {"components": {"gantry_1": {"axes": {"x": {"name": "x"}}}}}

    with pytest.raises(ValueError, match="Modbus axis 'x'.*'ip'"):
        parse_component(config, "gantry_1")
