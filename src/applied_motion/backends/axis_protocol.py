# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Structural protocol that all axis backend implementations must satisfy."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Axis(Protocol):
    """Structural interface for a single controllable axis.

    Both :class:`~applied_motion.gantry.backend.edcon_axis.EdconAxis` (Modbus/EDCON backend) and
    :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis` (FPosBAPI backend)
    satisfy this protocol structurally — no explicit inheritance required.

    All position values are in **millimetres (mm)**.
    All velocity values are in **mm/s**.
    """

    name: str

    def move(self, position: float, velocity: float, **kwargs) -> bool:
        """Move the axis to *position* at *velocity*.

        Args:
            position: Target position in mm (absolute or relative depending on
                ``position_type`` kwarg).
            velocity: Move speed in mm/s.
            **kwargs: Backend-specific options (e.g. ``position_type``,
                ``timeout``).

        Returns:
            ``True`` if the move completed successfully.
        """
        ...

    def home(self) -> None:
        """Run the axis homing/referencing sequence."""
        ...

    def get_current_axis_position(self) -> float:
        """Return the current axis position in mm.

        Returns:
            Current position in mm.
        """
        ...

    def is_homed(self) -> bool:
        """Return whether the gantry has been homed.

        Returns:
            ``True`` if gantry is homed/referenced; ``False`` if gantry has not been homed/referenced.
        """
        ...

    def stopped(self) -> bool:
        """Return ``True`` when the axis is not currently in motion.

        Returns:
            ``True`` if the axis has stopped; ``False`` if motion is ongoing.
        """
        ...

    def acknowledge_faults(self):
        """Clear errors."""
        # TODO: This shouldn't be exposed to end-users. This is done automatically for almost every motion task to proceed and is part of an internal interface.
        ...

    def enable_powerstage(self):
        """Turn on torque to motor controlling this axis."""
        # TODO: This shouldn't be exposed to end-users. This is done automatically for almost every motion task to proceed and is part of an internal interface.
        ...

    def disable_powerstage(self):
        """Turn off torque to motor controlling this axis."""
        ...

    def current_position(self):
        """Return current position."""
        ...

    def current_velocity(self):
        """Return current velocity."""
        ...

    def jog_task(
        self,
        og_positive: bool = True,
        jog_negative: bool = False,
        incremental: bool = False,
        duration: float = 0.0,
    ) -> bool:
        """Jog axis.

        Parameters:
            jog_positive (bool): If true, jog in positive direction.
            jog_negative (bool): If true, jog in negative direction.

            duration (float): Optional duration in seconds.
                              A duration of 0 starts the task and returns immediately.

        Returns:
            bool: True if succesful, False otherwise
        """
        # TODO: Implement this functionality using corresponding FPosBAPI command

        ...

    def ready_for_motion(self) -> bool:
        """Return ``True`` when the axis is ready to accept a move command.

        Returns:
            ``True`` if the axis is enabled, homed, and fault-free.
        """
        ...
