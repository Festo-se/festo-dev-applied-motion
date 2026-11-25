# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Degree-based rotary EDCON axis implementation."""

from applied_motion.backends.edcon_axis import EdconAxis


class RotaryAxis(EdconAxis):
    """EDCON axis whose public position and velocity units are degrees.

    Rotary coordinates convert through revolutions and the commissioned EDCON
    position/velocity scales. The commissioned modulo mode and clipping bounds
    are read from the drive before the power stage is enabled.
    """

    _coordinate_kind = "rotary"
    _position_unit_label = "degrees"
    _position_limit_inset = 0.0
    _default_input_pos_unit = {"angle": {"unit": "degree", "power": 1}}
    _default_input_vel_unit = {
        "angle": {"unit": "degree", "power": 1},
        "time": {"unit": "s", "power": -1},
    }

    def _read_axis_specific_configuration(self) -> None:
        """Read and validate the commissioned modulo mode and period."""
        requested_mode = self._read_integer_pnu(12113)
        active_mode = self._read_integer_pnu(12118)
        modulo_low = self._read_integer_pnu(832)
        modulo_high = self._read_integer_pnu(833)

        if requested_mode not in (0, 1) or active_mode not in (0, 1):
            raise ValueError("Unsupported EDCON modulo mode value")
        if requested_mode != active_mode:
            raise ValueError("EDCON requested and active modulo modes do not match")
        if modulo_high <= modulo_low:
            raise ValueError("EDCON modulo clipping period must be positive")

        self._modulo_enabled = active_mode == 1
        self._modulo_low_drive_units = modulo_low
        self._modulo_high_drive_units = modulo_high
        self._modulo_period_drive_units = modulo_high - modulo_low
        self._position_drive_units_per_revolution = 10 ** (-self._pos_scale_exp)
        self._velocity_drive_units_per_revolution_per_second = 10 ** (-self._vel_scale_exp)

    def _get_drive_position_bounds(self) -> tuple[int, int]:
        """Return the active software and modulo position-bound intersection."""
        software_low, software_high = super()._get_drive_position_bounds()
        if not self._modulo_enabled:
            return software_low, software_high
        return (
            max(software_low, self._modulo_low_drive_units),
            min(software_high, self._modulo_high_drive_units),
        )

    def _valid_position(self, position: int | float, input_unit: dict, invert: bool = False) -> float:
        """Convert between degrees and EDCON drive position units."""
        if input_unit != getattr(self, "input_pos_unit", self._default_input_pos_unit):
            raise ValueError("Rotary position conversion requires degree units")
        scale = self._position_drive_units_per_revolution
        if invert:
            return position * 360.0 / scale
        return position / 360.0 * scale

    def _valid_velocity(self, velocity: int | float, input_unit: dict, invert: bool = False) -> float:
        """Convert between degrees/second and EDCON drive velocity units."""
        if input_unit != self.input_vel_unit:
            raise ValueError("Rotary velocity conversion requires degrees/second units")
        scale = self._velocity_drive_units_per_revolution_per_second
        if invert:
            return velocity * 360.0 / scale
        return velocity / 360.0 * scale
