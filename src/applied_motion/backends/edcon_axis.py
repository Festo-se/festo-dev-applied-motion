# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Modbus TCP axis backend — direct per-drive edcon/CMMT connection.

[`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis] is the original Festo gantry axis implementation.  It
connects directly to an individual CMMT servo/stepper drive via
Modbus TCP using the ``festo-edcon`` library, and exposes a millimetre-based
public API that satisfies
[`Axis`][applied_motion.backends.axis_protocol.Axis].

This backend is appropriate when each drive is individually addressable on the
network and [`Gantry`][applied_motion.applied_motion.Gantry] manages concurrency in
Python.

For coordinated multi-axis motion managed by a CECC-X PLC using the Festo Easy
Positioning API, use [`FPosBAxis`][applied_motion.backends.fposbapi_axis.FPosBAxis]
with the FPosBAPI backend instead.

"""

import logging
from math import inf, isnan

from threading import Thread

from edcon.edrive.com_modbus import ComModbus
from edcon.edrive.motion_handler import MotionHandler

logger = logging.getLogger(__name__)

# Drive parameter (PNU) reference — useful when extending move()/limit handling:
#   working stroke: P1.1196.0.0, PNU 11298.0
#   negative limit position: P1.4629.0.0, PNU 11584.0
#   positive limit position: P1.4630.0.0, PNU 11585.0
#   Parameters with units m or mm in FAS Config sets
#   Max Search Stroke in positive direction: P1.8412.0.0, PNU 11730.0
#   Max Search Stroke in negative direction: P1.8413.0.0, PNU 11731.0
#   Axis zero point offset: P1.8416.0.0, PNU 11734.0
#   Offset position relative: P1.102222.0.0, PNU 13072.0
#   Limit value remaining distance: P1.4685.0.0, PNU 11627.0
# TODO: INDICATE WHICH FIRMWARE VERSION THIS IS SUPPORTEDBY (DEPENDENT ON EDCON)


# TODO: Process Data communnication failed error on exiting a running program where drive is connected
class EdconAxis(MotionHandler):
    """Modbus TCP axis: direct festo-edcon ``MotionHandler`` connection to one drive.

    Wraps low-level Modbus communication and position/velocity unit conversion
    behind a linear millimetre-based API. Subclasses can supply alternate
    coordinate conversions while reusing the connection and motion lifecycle.

    Attributes:
        name: Human-readable label for this axis (e.g. ``"X"``).
        ip: IPv4 address of the drive's Modbus TCP endpoint.
        com: Active `ComModbus` connection.
        max_speed: Maximum achievable speed in drive velocity units.
        max_position: Maximum achievable position in the axis's public unit.
        min_position: Minimum achievable position in the axis's public unit.
    """

    max_position: float
    min_position: float
    max_velocity: float
    min_velocity: float
    max_position_fas_units: int
    min_position_fas_units: int
    max_velocity_fas_units: int
    min_velocity_fas_units: int

    _coordinate_kind = "linear"
    _position_unit_label = "mm"
    _position_limit_inset = 1.0
    _default_input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
    _default_input_vel_unit = {
        "distance": {"unit": "m", "power": 1, "power_of_ten": -3},
        "time": {"unit": "s", "power": -1, "power_of_ten": 1},
    }

    def __init__(
        self,
        name: str,
        ip: str,
        run_referencing: bool = False,
        max_position: float = inf,
        min_position: float = -inf,
    ) -> None:
        """Initialise the axis and establish a Modbus connection.

        Creates the `ComModbus` connection,
        initialises the parent `MotionHandler`,
        reads the software-limit PNUs, and optionally triggers homing.

        Args:
            name: Human-readable axis label used in log messages and equality
                checks.  Axis labels are unique within a gantry.
            ip: IPv4 address of the Festo drive's Modbus TCP interface.
            run_referencing: When ``True``, perform a homing (referencing)
                sequence during construction.  Defaults to ``False``.
            max_position: Optional max position limitation, if the drive is restricted
                more than the internal SW limit position check. This avoids an issue where
                traversing toward the actual endstop is interrupted because the interia of
                the motion causes the drive to overshoot the limit position, throwing an error
                and interrupting the power stage on state.
            min_position: Optional min position limitation, if the drive is restricted
                more than the internal SW limit position check. This avoids an issue where
                traversing toward the actual endstop is interrupted because the interia of
                the motion causes the drive to overshoot the limit position, throwing an error
                and interrupting the power stage on state.
        """
        self.name = name
        self.ip = ip
        self._closed = False
        self._power_stage_enable_attempted = False

        try:
            self.com = ComModbus(self.ip)
            super().__init__(self.com)
            self.acknowledge_faults()
            self.acknowledge_faults()
            # self.home()
            logger.info("Axis '%s': initialized", self.name)
            self.configure_software_limit_switch(True)
            logger.info("Axis '%s': software limit switch configured", self.name)

            self._neg_sw_limit = self.com.read_pnu(834)
            self._pos_sw_limit = self.com.read_pnu(835)
            self._min_vel = self.com.read_pnu(11212)
            self._max_vel = self.com.read_pnu(11213)
            self._pos_scale_exp = self.com.read_pnu(11724)
            self._vel_scale_exp = self.com.read_pnu(11725)

            self.input_pos_unit = self._default_input_pos_unit
            self.input_vel_unit = self._default_input_vel_unit
            self._read_axis_specific_configuration()

            if isnan(min_position) or isnan(max_position):
                raise ValueError("Configured position limits cannot be NaN")

            drive_min, drive_max = self._get_drive_position_bounds()
            effective_min = max(min_position, self._valid_position(drive_min, self.input_pos_unit, invert=True))
            effective_max = min(max_position, self._valid_position(drive_max, self.input_pos_unit, invert=True))
            if effective_min > effective_max:
                raise ValueError("Configured and EDCON drive position limits do not overlap")
            if effective_min != effective_max:
                effective_min += self._position_limit_inset
                effective_max -= self._position_limit_inset
            if effective_min > effective_max:
                raise ValueError("Effective position limits are empty after applying the axis inset")
            self.min_position = effective_min  # TODO: Fix overshooting following error in drive control
            self.max_position = effective_max

            self.max_velocity = self._valid_velocity(self._max_vel, self.input_vel_unit, invert=True)
            self.min_velocity = self._valid_velocity(self._min_vel, self.input_vel_unit, invert=True)

            logger.info(
                "Axis '%s': SW limits loaded — neg=%s, pos=%s (drive units)",
                self.name,
                self._neg_sw_limit,
                self._pos_sw_limit,
            )
            logger.info(
                "Axis '%s': SW limits loaded — neg=%s, pos=%s (standard units)",
                self.name,
                self.min_position,
                self.max_position,
            )

            if self.fault_present():
                logger.warning("Axis '%s': fault present on init", self.name)
            logger.debug("Axis '%s' fault string: %s", self.name, self.fault_string())
            logger.debug("Axis '%s' current fault code: %s", self.name, self.current_fault_code())
            self.acknowledge_faults()
            self._power_stage_enable_attempted = True
            self.enable_powerstage()

            if run_referencing:
                logger.info("Axis '%s': referencing requested during initialization", self.name)
                self.home()
        except Exception:
            try:
                self.close()
            except Exception:
                logger.exception("Axis '%s': cleanup after initialization failure failed", self.name)
            raise

    def _read_axis_specific_configuration(self) -> None:
        """Load coordinate-specific drive state before enabling the power stage."""

    def _get_drive_position_bounds(self) -> tuple[int, int]:
        """Return the drive position bounds in raw EDCON units."""
        return self._neg_sw_limit, self._pos_sw_limit

    def _read_integer_pnu(self, pnu: int) -> int:
        """Read an integer PNU value."""
        return int(self.com.read_pnu(pnu))

    def close(self) -> None:
        """Disable the power stage and release this axis's Modbus I/O.

        Cleanup is attempted once, even if one of the operations fails. The
        first failure is re-raised after both cleanup operations are attempted.

        Raises:
            Exception: The first error raised while disabling the power stage
                or stopping Modbus I/O.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True

        first_error: Exception | None = None
        if getattr(self, "_power_stage_enable_attempted", False):
            try:
                self.disable_powerstage()
            except Exception as exc:
                logger.exception("Axis '%s': failed to disable power stage during close", self.name)
                first_error = exc

        com = getattr(self, "com", None)
        if com is not None and getattr(com, "io_thread", None) is not None:
            try:
                com.stop_io()
            except Exception as exc:
                logger.exception("Axis '%s': failed to stop Modbus I/O during close", self.name)
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    def __del__(self) -> None:
        """Best-effort fallback for axes that were not explicitly closed."""
        try:
            self.close()
        except Exception:
            logger.exception("Axis '%s': best-effort cleanup failed", getattr(self, "name", "unknown"))
        try:
            super().__del__()
        except Exception:
            logger.debug("Axis '%s': parent destructor cleanup failed", getattr(self, "name", "unknown"), exc_info=True)

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the axis."""
        return f"EdconAxis(name={self.name!r}, ip={self.ip!r})"

    def __eq__(self, other: object) -> bool:
        """Return True when *other* represents the same physical axis.

        Args:
            other: Object to compare against.

        Returns:
            ``True`` if *other* is a [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis] with the same
            ``name`` and ``ip``; ``False`` otherwise.

        Raises:
            NotImplementedError: If *other* is not a [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis].
        """
        if not isinstance(other, EdconAxis):
            return False
        return self._coordinate_kind == other._coordinate_kind and self.name == other.name and self.ip == other.ip

    def __hash__(self) -> int:
        """Return a hash derived from the axis identity fields.

        Uses the same fields as [`__eq__`][applied_motion.backends.edcon_axis.EdconAxis.__eq__] so that equal axes have
        equal hashes, making [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis] safe to use in sets and
        as dict keys.
        """
        return hash((self._coordinate_kind, self.name, self.ip))

    # TODO: Use attrs?
    def home(self) -> bool:
        """Home the axis by running a referencing task.

        Acknowledges any faults, enables the power stage, and then
        executes a blocking referencing task to establish the axis zero
        point.
        """
        logger.info("Axis '%s': starting homing sequence", self.name)
        self.acknowledge_faults()
        self._power_stage_enable_attempted = True
        self.enable_powerstage()
        res = self.referencing_task(nonblocking=False)

        logger.info("Axis '%s': homing result=%s", self.name, res)
        logger.info("Axis '%s': homing complete", self.name)
        return res

    def is_homed(self) -> bool:
        """Checks whether drive has been homed and has a valid frame of reference.

        Thin wrapper for interface consistency around `MotionHandler.referenced()`.

        """
        return self.referenced()

    def current_position(self):
        """Transparent wrapper around ``MotionHandler.current_position``.

        # TODO: is this necessary?

        Returns the raw position value in the drive's **internal unit system**
        (drive units, whose scale is determined by PNU 11724 and varies by
        firmware/configuration). Use
        [`get_current_axis_position`][applied_motion.backends.edcon_axis.EdconAxis.get_current_axis_position] when you need the position in a
        consistent, human-readable axis unit.
        """
        logger.warning(
            "Axis '%s': current_position() returns a raw drive-unit value — "
            "unit depends on drive configuration (PNU 11724). "
            "Call get_current_axis_position() for a consistent %s value.",
            self.name,
            self._position_unit_label,
        )
        return super().current_position()

    @property
    def max_speed(self) -> float:
        """Return largest allowed speed magnitude for this axis in native units/s.

        This derives from configured signed velocity bounds and returns
        absolute maximum magnitude, independent of direction.

        Returns:
            Maximum absolute velocity limit in the axis's native velocity unit.
        """
        return max(abs(self.min_velocity), abs(self.max_velocity))

    def get_current_axis_position(self) -> float:
        """Return the current axis position in the axis's native output unit.

        Reads the raw drive-unit position from ``MotionHandler.current_position``,
        then converts it to the axis's public unit via
        ``_valid_position(..., invert=True)``.
        The drive's own PNU-reported position-unit scale is used for the
        conversion, so the result is always consistent regardless of how the
        drive was configured.

        Internal methods (`_check_overshoot`, unit converters) continue to
        use ``super().current_position()`` directly so their drive-unit arithmetic
        is unaffected.
        """
        input_unit = getattr(self, "input_pos_unit", self._default_input_pos_unit)
        return self._valid_position(super().current_position(), input_unit, invert=True)

    def _clamped_target(self, position, positioning_type):
        current = self.get_current_axis_position()

        if positioning_type:
            target = position
        else:
            target = current + position

        # Target and configured limits are in this axis's public unit.
        # if self.min_position <= target <= self.max_position:
        #     return target  # already in range, no change needed
        clamped_target = max(self.min_position, min(self.max_position, target))

        if positioning_type:
            return clamped_target
        else:
            # Convert clamped absolute target back to a relative delta
            return clamped_target - current

    def move(self, position: int | float, velocity: int | float, timeout: int | None = None, **kwargs) -> bool:
        """Move the axis to a specified position with a given velocity.

        Args:
            position: Target position in the axis's native unit. Converted
                internally to drive units before the motion command is issued.
            velocity: Move velocity in the axis's native unit per second.
                Converted internally to drive units.
            timeout: Optional time limit in seconds for the move.  When provided,
                the motion is launched in a background thread and a stop command
                is issued if the thread has not finished by the deadline.  When
                ``None`` (default), the call blocks until motion completes.
            **kwargs: Optional keyword overrides.  Recognised keys:

                - ``position_type`` (``str``): ``"absolute"`` for an absolute
                  target position or ``"relative"`` for a relative displacement.
                  When omitted the move is treated as **absolute**.

        Returns:
            ``True`` if the motion task succeeded, ``False`` otherwise.
        """
        # TODO: check valid move parameters against internal set values
        # e.g. Position limit, velocity limit, acceleration limit, etc.
        # if not valid, raise exception
        pos_type = kwargs.get("position_type", "absolute")
        logger.debug(
            "Axis '%s': entering move — raw position=%s  raw velocity=%s raw timeout=%s raw position_type=%s",
            self.name,
            position,
            velocity,
            timeout,
            pos_type,
        )
        positioning_type = pos_type == "absolute"

        if self.min_position == self.max_position:
            current_position = self.get_current_axis_position()
            target_position = position if positioning_type else current_position + position
            # TODO: WARN, THIS IS A WEIRD STATE
            return target_position == current_position == self.min_position

        clamped_position = self._clamped_target(position, positioning_type)
        logger.debug("Axis '%s': processing move — clamped_position=%s", self.name, clamped_position)
        validated_position = int(self._valid_position(clamped_position, self.input_pos_unit))
        validated_velocity = int(self._valid_velocity(velocity, self.input_vel_unit))
        logger.debug(
            "Axis '%s': processing move — validated_position=%s  validated_velocity=%s",
            self.name,
            validated_position,
            validated_velocity,
        )
        # self.configure_continuous_update(True) # continous update so that new position tasks can be started while still in motion
        #
        # TODO: Check faults and abort if critical fault encountered

        # self.acknowledge_faults()

        logger.debug(
            "Axis '%s': fault_string=%s  fault_code=%s", self.name, self.fault_string(), self.current_fault_code()
        )
        validated_position = self._check_overshoot(validated_position, absolute=positioning_type)
        logger.debug(
            "Axis '%s': clamped_position=%s  validated_velocity=%s", self.name, validated_position, validated_velocity
        )
        # Skip if already at target (absolute) or zero delta (relative).
        at_target = (
            abs(validated_position - int(super().current_position())) == 0
            if positioning_type
            else abs(validated_position) == 0
        )
        if at_target:
            logger.info("Axis '%s': already at target drive position %s, skipping move", self.name, validated_position)
            return True
        result: bool = False
        # TODO: Check powerstage enabled, attempt enable if not
        # TODO:
        # TODO: Check for CRITICAL error stages,
        # TODO: ack faults,
        # TODO: then move.
        # TODO: Incorporate jog mode into this function?
        self.acknowledge_faults()
        self._power_stage_enable_attempted = True
        self.enable_powerstage()
        if timeout is None:
            result = self.position_task(
                validated_position,
                validated_velocity,
                absolute=positioning_type,
                nonblocking=False,
            )
        else:
            _result_box: list[bool] = []
            move_thread = Thread(
                target=lambda: _result_box.append(
                    self.position_task(
                        validated_position,
                        validated_velocity,
                        absolute=positioning_type,
                        nonblocking=False,
                    )
                )
            )
            move_thread.start()
            move_thread.join(timeout=timeout)
            if move_thread.is_alive():
                logger.warning("Axis '%s': move timed out after %ss — sending stop motion task", self.name, timeout)
                self.stop_motion_task()
                # Wait for the thread to actually exit before returning; 50 ms is not enough.
                move_thread.join(timeout=timeout)
                if move_thread.is_alive():
                    logger.error(
                        f"Axis '%s': position_task thread still alive {timeout}s after stop command", self.name
                    )
            else:
                result = _result_box[0] if _result_box else False  # TODO: AllTrue?

        logger.info("Axis '%s': motion task complete, result=%s", self.name, result)
        return result

    def _check_overshoot(self, validated_position: int, absolute: bool) -> int:
        """Clamp *validated_position* to the axis's effective drive limits.

        The public effective limits already combine configured, software, and
        (for modulo-enabled rotary axes) modulo clipping bounds. For relative
        moves, resolve the target against the current position first, then
        clamp the *delta* so the resulting absolute target stays in range.

        Args:
            validated_position: Drive-unit position that has already passed
                through `_valid_position`.  May be absolute or relative
                depending on *absolute*.
            absolute: ``True`` if *validated_position* is an absolute target;
                ``False`` if it is a relative displacement from the current
                position.

        Returns:
            Clamped drive-unit position (absolute) or clamped displacement
            (relative) that is guaranteed to keep the axis within the stored
            software limits.
        """
        configured_neg_limit = int(self._valid_position(self.min_position, self.input_pos_unit))
        configured_pos_limit = int(self._valid_position(self.max_position, self.input_pos_unit))

        current_pos = 0
        if absolute:
            target = validated_position
        else:
            current_pos = super().current_position()  # snapshot once; position may change under motion
            target = current_pos + validated_position

        if configured_neg_limit <= target <= configured_pos_limit:
            logger.debug(
                "Axis '%s': clamp diagnostics — configured(%s)=[%s, %s], drive_raw=[%s, %s], target_raw=%s, no clamp applied",
                self.name,
                self._position_unit_label,
                self.min_position,
                self.max_position,
                self._neg_sw_limit,
                self._pos_sw_limit,
                target,
            )
            return validated_position  # return original delta (relative) or target (absolute — same value)

        clamped_target = int(max(configured_neg_limit, min(configured_pos_limit, target)))
        scaled_target = self._valid_position(target, self.input_pos_unit, invert=True)
        scaled_clamped_target = self._valid_position(clamped_target, self.input_pos_unit, invert=True)
        logger.warning(
            "Axis '%s': requested target %s %s is outside configured limits [%s %s, %s %s] — clamping to %s %s",
            self.name,
            scaled_target,
            self._position_unit_label,
            self.min_position,
            self._position_unit_label,
            self.max_position,
            self._position_unit_label,
            scaled_clamped_target,
            self._position_unit_label,
        )
        logger.debug(
            "Axis '%s': clamp diagnostics — configured(%s)=[%s, %s], drive_raw=[%s, %s], target_raw=%s, clamped_raw=%s",
            self.name,
            self._position_unit_label,
            self.min_position,
            self.max_position,
            self._neg_sw_limit,
            self._pos_sw_limit,
            target,
            clamped_target,
        )

        if absolute:
            return clamped_target
        else:
            return clamped_target - current_pos

    def _valid_position(self, position: int | float, input_unit: dict, invert: bool = False) -> float:
        """Convert *position* between the caller's unit system and the drive's unit system.

        Reads PNU 11724 to determine the drive's current position scale factor
        (power-of-ten exponent for metres) and applies the resulting scaling
        factor to *position*.

        Args:
            position: Numeric position value expressed in *input_unit*.
            input_unit: Unit descriptor dict with a ``"distance"`` key whose
                value contains ``"unit"``, ``"power"``, and ``"power_of_ten"``
                entries (e.g. ``{"distance": {"unit": "m", "power": 1,
                "power_of_ten": -3}}`` for millimetres).
            invert: When ``False`` (default), convert from *input_unit* to drive
                units.  When ``True``, convert from drive units to *input_unit*.

        Returns:
            Converted numeric value in the target unit system.
        """
        # TODO: Investigate misura, QuantiPhy, pint packages for units? Need **lightweight** package option

        system_unit = {
            "distance": {
                "unit": "m",
                "power": 1,
                "power_of_ten": self._pos_scale_exp,
            }
        }

        if system_unit == input_unit:
            return position

        scale = 10 ** (input_unit["distance"]["power_of_ten"] - system_unit["distance"]["power_of_ten"])
        return position * (scale ** (2 * int((not (invert))) - 1))

    def _valid_velocity(self, velocity: int | float, input_unit: dict, invert: bool = False) -> float:
        """Convert *velocity* between the caller's unit system and the drive's unit system.

        Reads PNU 11725 to determine the drive's current velocity scale factor
        and applies the resulting scaling factor to *velocity*.

        Args:
            velocity: Numeric velocity value expressed in *input_unit*.
            input_unit: Unit descriptor dict with ``"distance"`` and ``"time"``
                keys, each containing ``"unit"``, ``"power"``, and
                ``"power_of_ten"`` entries.
            invert: When ``False`` (default), convert from *input_unit* to drive
                units.  When ``True``, convert from drive units to *input_unit*.

        Returns:
            Converted numeric value in the target unit system.
        """
        system_unit = {
            "distance": {
                "unit": "m",
                "power": 1,
                "power_of_ten": self._vel_scale_exp,
            }
        }

        if system_unit == input_unit:
            return velocity

        scale = 10 ** (input_unit["distance"]["power_of_ten"] - system_unit["distance"]["power_of_ten"])
        return velocity * (scale ** (2 * int((not (invert))) - 1))
