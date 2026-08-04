# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Modbus TCP axis backend — direct per-drive edcon/CMMT connection.

:class:`EdconAxis` is the original Festo gantry axis implementation.  It
connects directly to an individual CMMT servo/stepper drive via
Modbus TCP using the ``festo-edcon`` library, and exposes a millimetre-based
public API that satisfies
:class:`~applied_motion.backends.axis_protocol.Axis`.

This backend is appropriate when each drive is individually addressable on the
network and :class:`~applied_motion.gantry.Gantry` manages concurrency in
Python.

For coordinated multi-axis motion managed by a CECC-X PLC using the Festo Easy
Positioning API, use :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis`
with the FPosBAPI backend instead.

"""

import logging
import time
from math import inf

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


# TODO: Process Data communnication failed error on exiting a running program where drive is connected
class EdconAxis(MotionHandler):
    """Modbus TCP axis: direct festo-edcon ``MotionHandler`` connection to one drive.

    Wraps low-level Modbus communication and position/velocity unit conversion
    behind a millimetre-based public API.  All position values accepted and
    returned by the public methods use **millimetres (mm)** as their unit.

    Attributes:
        name: Human-readable label for this axis (e.g. ``"X"``).
        ip: IPv4 address of the drive's Modbus TCP endpoint.
        com: Active :class:`~edcon.edrive.com_modbus.ComModbus` connection.
        max_speed: Maximum achievable speed in drive velocity units.
        max_position: Maximum achievable position in mm.
        min_position: Minimum achievable position in mm.
    """

    max_position: float
    min_position: float
    max_velocity: float
    min_velocity: float
    max_position_fas_units: int
    min_position_fas_units: int
    max_velocity_fas_units: int
    min_velocity_fas_units: int

    def __init__(
        self, name: str, ip: str, run_referencing: bool = False, max_position: float = inf, min_position: float = -inf
    ) -> None:
        """Initialise the axis and establish a Modbus connection.

        Creates the :class:`~edcon.edrive.com_modbus.ComModbus` connection,
        initialises the parent :class:`~edcon.edrive.motion_handler.MotionHandler`,
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
        self.com = ComModbus(self.ip)
        super().__init__(self.com)
        self.acknowledge_faults()

        self.acknowledge_faults()
        # self.home()
        logger.info("Axis '%s': initialized", self.name)
        self.configure_software_limit_switch(True)
        logger.info("Axis '%s': software limit switch configured", self.name)

        self._neg_sw_limit: int = self.com.read_pnu(834)
        self._pos_sw_limit: int = self.com.read_pnu(835)

        self._min_vel: int = self.com.read_pnu(11212)
        self._max_vel: int = self.com.read_pnu(11213)
        self.input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
        self.input_vel_unit = {
            "distance": {"unit": "m", "power": 1, "power_of_ten": -3},
            "time": {"unit": "s", "power": -1, "power_of_ten": 1},
        }
        self.max_position = min(
            max_position, self._valid_position(self._pos_sw_limit, self.input_pos_unit, invert=True)
        )  # TODO: Get these from config, compare with SW limits and take most restrictive superset
        self.min_position = max(
            min_position, self._valid_position(self._neg_sw_limit, self.input_pos_unit, invert=True)
        )  # TODO: Get these from config, compare with SW limits and take most restrictive superset
        self.max_velocity = self._valid_velocity(
            self._max_vel, self.input_vel_unit, invert=True
        )  # TODO: Get these from config, compare with SW limits and take most restrictive superset
        self.min_velocity = self._valid_velocity(
            self._min_vel, self.input_vel_unit, invert=True
        )  # TODO: Get these from config, compare with SW limits and take most restrictive superset

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
        self.enable_powerstage()

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the axis."""
        return f"EdconAxis(name={self.name!r}, ip={self.ip!r})"

    def __eq__(self, other: object) -> bool:
        """Return True when *other* represents the same physical axis.

        Args:
            other: Object to compare against.

        Returns:
            ``True`` if *other* is a :class:`EdconAxis` with the same
            ``name`` and ``ip``; ``False`` otherwise.

        Raises:
            NotImplementedError: If *other* is not a :class:`EdconAxis`.
        """
        if not isinstance(other, EdconAxis):
            raise NotImplementedError("Cannot compare EdconAxis with non-EdconAxis object")
        return self.name == other.name and self.ip == other.ip

    def __hash__(self) -> int:
        """Return a hash derived from the axis identity fields.

        Uses the same fields as :meth:`__eq__` so that equal axes have
        equal hashes, making :class:`EdconAxis` safe to use in sets and
        as dict keys.
        """
        return hash((self.name, self.ip))

    # TODO: Use attrs?
    def home(self) -> None:
        """Home the axis by running a referencing task.

        Acknowledges any faults, enables the power stage, and then
        executes a blocking referencing task to establish the axis zero
        point.
        """
        logger.info("Axis '%s': starting homing sequence", self.name)
        self.acknowledge_faults()
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
        firmware/configuration — typically 0.001 mm per unit).  Use
        :meth:`get_current_axis_position` when you need the position in a
        consistent, human-readable unit (mm).
        """
        logger.warning(
            "Axis '%s': current_position() returns a raw drive-unit value — "
            "unit depends on drive configuration (PNU 11724). "
            "Call get_current_axis_position() for a consistent mm value.",
            self.name,
        )
        return super().current_position()

    def get_current_axis_position(self) -> float:
        """Return the current axis position in the library's canonical output unit (mm).

        Reads the raw drive-unit position from ``MotionHandler.current_position``,
        then converts it to mm via :meth:`_valid_position` with ``invert=True``.
        The drive's own PNU-reported position-unit scale is used for the
        conversion, so the result is always consistent regardless of how the
        drive was configured.

        Internal methods (:meth:`_check_overshoot`, unit converters) continue to
        use ``super().current_position()`` directly so their drive-unit arithmetic
        is unaffected.
        """
        _mm_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
        return self._valid_position(super().current_position(), _mm_unit, invert=True)

    def move(self, position: int | float, velocity: int | float, timeout: int | None = None, **kwargs) -> bool:
        """Move the axis to a specified position with a given velocity.

        Args:
            position: Target position in millimetres (mm).  Converted internally
                to drive units before the motion command is issued.
            velocity: Move velocity in mm/s.  Converted internally to drive units.
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
        positioning_type = kwargs.get("position_type", "absolute") == "absolute"

        input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
        input_vel_unit = {
            "distance": {"unit": "m", "power": 1, "power_of_ten": -3},
            "time": {"unit": "s", "power": -1, "power_of_ten": 1},
        }
        try:
            validated_position = int(self._valid_position(position, input_pos_unit))
        except Exception as e:
            logger.error("Axis '%s': failed to validate position — %s", self.name, e)
            validated_position = -5

        try:
            validated_velocity = int(self._valid_velocity(velocity, input_vel_unit))
        except Exception as e:
            logger.error("Axis '%s': failed to validate velocity — %s", self.name, e)
            validated_velocity = 5
        logger.debug(
            "Axis '%s': entering move — position=%s  velocity=%s", self.name, validated_position, validated_velocity
        )
        # self.configure_continuous_update(True) # continous update so that new position tasks can be started while still in motion
        #
        # TODO: Check faults and abort if critical fault encountered

        # self.acknowledge_faults()

        logger.debug(
            "Axis '%s': fault_string=%s  fault_code=%s", self.name, self.fault_string(), self.current_fault_code()
        )
        validated_position = self._check_overshoot(validated_position, absolute=positioning_type)

        result: bool = False
        # TODO: Check powerstage enabled, attempt enable if not
        # TODO:
        # TODO: Check for CRITICAL error stages,
        # TODO: ack faults,
        # TODO: then move.
        # TODO: Incorporate jog mode into this function?
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
                time.sleep(0.05)
            else:
                result = _result_box[0] if _result_box else False  # TODO: AllTrue?

        logger.info("Axis '%s': motion task complete, result=%s", self.name, result)
        return result

        # working stroke: P1.1196.0.0, PNU 11298.0
        # negative limit position: P1.4629.0.0, PNU 11584.0
        # positive limit position: P1.4630.0.0, PNU 11585.0
        # Parameters with units m or mm in FAS Config sets
        # Max Search Stroke in positive direction: P1.8412.0.0, PNU 11730.0
        # Max Search Stroke in negative direction: P1.8413.0.0, PNU 11731.0
        # Axis zero point offset: P1.8416.0.0, PNU 11734.0
        # Offset position relative: P1.102222.0.0,PNU 13072.0
        # Limit value remaining distance: P1.4685.0.0, PNU 11627.0
        # Limit value following error:
        # Stroke limit positive for detecting fixed stop:
        # Stroke limit negative for detecting fixed stop:
        # Hysteresis:
        #

    def _check_overshoot(self, validated_position: int, absolute: bool) -> int:
        """Clamp *validated_position* to the device's stored SW limits.

        For absolute moves, clamp directly against
        ``[_neg_sw_limit, _pos_sw_limit]``.
        For relative moves, resolve the target against the current position
        first, then clamp the *delta* so the resulting absolute target stays
        in range.

        Args:
            validated_position: Drive-unit position that has already passed
                through :meth:`_valid_position`.  May be absolute or relative
                depending on *absolute*.
            absolute: ``True`` if *validated_position* is an absolute target;
                ``False`` if it is a relative displacement from the current
                position.

        Returns:
            Clamped drive-unit position (absolute) or clamped displacement
            (relative) that is guaranteed to keep the axis within the stored
            software limits.
        """
        if absolute:
            target = validated_position
        else:
            target = self.current_position() + validated_position

        if self._neg_sw_limit <= target <= self._pos_sw_limit:
            return validated_position  # already in range, no change needed

        clamped_target = max(self._neg_sw_limit, min(self._pos_sw_limit, target))
        logger.warning(
            "Axis '%s': requested target %s is outside SW limits [%s, %s] — clamping to %s",
            self.name,
            target,
            self._neg_sw_limit,
            self._pos_sw_limit,
            clamped_target,
        )

        if absolute:
            return clamped_target
        else:
            # Convert clamped absolute target back to a relative delta
            return clamped_target - self.current_position()

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
                "power_of_ten": self.com.read_pnu(11724),
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
                "power_of_ten": self.com.read_pnu(11725),
            }
        }

        if system_unit == input_unit:
            return velocity

        scale = 10 ** (input_unit["distance"]["power_of_ten"] - system_unit["distance"]["power_of_ten"])
        return velocity * (scale ** (2 * int((not (invert))) - 1))
