"""Offline contract tests for the rotary hardware commissioning config."""

import json
from math import inf
from pathlib import Path
from unittest.mock import MagicMock

from applied_motion.configurator_adapter import build_gantry_from_config

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "rotary-hardware-config.json"


def test_rotary_hardware_fixture_is_valid_configurator_gantry(monkeypatch):
    rotary_axis = MagicMock()
    axis_calls = []

    def create_rotary_axis(**kwargs):
        axis_calls.append(kwargs)
        return rotary_axis

    monkeypatch.setattr("applied_motion.gantry_factory.RotaryAxis", create_rotary_axis)

    construction = build_gantry_from_config(_FIXTURE)
    try:
        assert list(construction.axes) == ["R"]
        assert construction.axes["R"] is rotary_axis
    finally:
        construction.close()

    assert axis_calls == [
        {
            "name": "R",
            "ip": "192.168.0.4",
            "run_referencing": False,
            "min_position": -inf,
            "max_position": inf,
        }
    ]
    rotary_axis.close.assert_called_once_with()


def test_rotary_hardware_fixture_has_no_test_only_configurator_metadata():
    with _FIXTURE.open(encoding="utf-8") as file:
        config = json.load(file)

    assert config["component_config"]["metadata"] == {}
