# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Structural protocol that all axis backend implementations must satisfy."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Axis(Protocol):
    """Structural interface for a single controllable axis.

    Both :class:`~festo_dev_applied_motion.gantry.backend.edcon_axis.EdconAxis` (Modbus/EDCON backend) and
    :class:`~festo_dev_applied_motion.backends.fposapi_axis.FPosAxis` (FPosAPI backend)
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

    def stopped(self) -> bool:
        """Return ``True`` when the axis is not currently in motion.

        Returns:
            ``True`` if the axis has stopped; ``False`` if motion is ongoing.
        """
        ...

    def ready_for_motion(self) -> bool:
        """Return ``True`` when the axis is ready to accept a move command.

        Returns:
            ``True`` if the axis is enabled, homed, and fault-free.
        """
        ...
