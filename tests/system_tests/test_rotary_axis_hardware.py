"""System test for the configured rotary EDCON axis.

Run with ``uv run pytest -m hardware``. The default address comes from
``tests/fixtures/rotary-hardware-config.json``; ``ROTARY_AXIS_IP`` can
override it without changing the fixture.
"""

import json
import os
import socket
from pathlib import Path

import pytest

from applied_motion.backends.rotary_axis import RotaryAxis
from applied_motion.configurator_adapter import build_gantry_from_config

_CONFIG_PATH = Path(__file__).parent.parent / "fixtures" / "rotary-hardware-config.json"
_MODBUS_PORT = 502
_CONNECT_TIMEOUT_S = 2.0


def _is_reachable(ip: str) -> bool:
    """Return whether the configured Modbus endpoint accepts a TCP connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(_CONNECT_TIMEOUT_S)
        return connection.connect_ex((ip, _MODBUS_PORT)) == 0


@pytest.mark.hardware
def test_rotary_hardware_config_connects_and_homes_if_needed():
    """Build the rotary axis from the Configurator fixture and verify its live state."""
    with _CONFIG_PATH.open(encoding="utf-8") as file:
        config = json.load(file)

    component = config["component_config"]["components"]["gantry_1"]
    axis_config = component["axes"]["R"]
    ip_override = os.getenv("ROTARY_AXIS_IP")
    if ip_override:
        axis_config["ip"] = ip_override
    ip = axis_config["ip"]

    if not _is_reachable(ip):
        pytest.skip(f"Rotary hardware axis not reachable at {ip}:{_MODBUS_PORT}")

    construction = build_gantry_from_config(config)
    try:
        axis = construction.axes["R"]
        assert isinstance(axis, RotaryAxis)
        assert axis.position_unit == "degrees"
        assert axis.velocity_unit == "degrees/s"
        assert axis.min_position < axis.max_position

        if not axis.is_homed():
            assert axis.home()

        assert axis.is_homed()
        assert axis.stopped()
        assert axis.ready_for_motion()
        position = axis.get_current_axis_position()
        assert axis.min_position <= position <= axis.max_position
    finally:
        construction.close()
