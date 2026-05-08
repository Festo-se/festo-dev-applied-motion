# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""FPosBAPI axis proxy — represents one axis of a CECC-X controlled gantry.

:class:`FPosBAxis` delegates all motion commands through a shared
:class:`~applied_motion.backends.fposbapi_client.FPosBAPIClient` socket connection
to the CECC-X PLC.  It satisfies the
:class:`~applied_motion.backends.axis_protocol.Axis` structural interface
and can be used anywhere a :class:`~applied_motion.gantry.backends.edcon_axis EdconAxis` is accepted
by :class:`~applied_motion.gantry.Gantry`.

Axis index convention (matches CECC-X CoDeSys program default):

* ``1`` — X axis
* ``2`` — Y axis
* ``3`` — Z axis

ROB_POS response field layout assumed by :meth:`FPosBAxis.get_current_axis_position`::

    msg_id, ROB_POS, x_mm, y_mm, z_mm, 0, NULL, SUCCESS

Position at field index ``axis_index + 1`` (1-based field 2 = X, 3 = Y, 4 = Z).

.. warning::
    :meth:`move` issues a ``SET_PAR 103`` (global speed) command immediately
    before ``MOVE_AXIS``.  These two commands are serialised by the shared
    :class:`~applied_motion.backends.fposbapi_client.FPosBAPIClient` lock, but they
    are **not** atomic — concurrent moves on different axes may interleave their
    ``SET_PAR`` calls.  Use :meth:`~applied_motion.gantry.Gantry.move_to`
    with a single-axis movement queue (not concurrent) when per-move velocity
    accuracy matters.
"""

import logging

from applied_motion.backends.fposbapi_client import FPosBAPIClient

logger = logging.getLogger(__name__)


class FPosBAxis:
    """FPosBAPI-backed representation of a single CECC-X gantry axis.

    Wraps ``MOVE_AXIS``, ``HOME``, and ``ROB_POS`` FPosBAPI commands behind the
    same public interface as :class:`~applied_motion.gantry.backends.edcon_axis.EdconAxis`.  All
    socket communication is performed via the shared *client* instance which
    must be owned by the parent :class:`~applied_motion.gantry.Gantry`.

    Attributes:
        name: Human-readable axis label, e.g. ``"X"``.
        index: 1-based axis number sent to ``MOV_AXIS`` (``1``=X, ``2``=Y,
            ``3``=Z by CECC-X convention).
    """

    def __init__(self, name: str, index: int, client: FPosBAPIClient) -> None:
        """Initialise the axis proxy.

        Args:
            name: Human-readable axis label used in log messages and equality
                checks.
            index: 1-based axis index for ``MOVE_AXIS`` commands.  Must match
                the axis numbering configured in the CECC-X CoDeSys program.
            client: Shared :class:`~applied_motion.backends.fposbapi_client.FPosBAPIClient`
                instance owned by the parent gantry.
        """
        self.name = name
        self.index = index
        self._client = client
        logger.info("FPosBAxis '%s' (index=%d) created", name, index)

    def move(self, position: float, velocity: float, timeout: int | None = None, **kwargs) -> bool:
        """Move this axis to *position* at *velocity*.

        Sets the global gantry speed (parameter 103) to *velocity* before
        issuing the ``MOVE_AXIS`` command.  The call blocks until the PLC
        reports ``SUCCESS``.

        Args:
            position: Target position in mm.  Interpreted as absolute by
                default; pass ``position_type="relative"`` in *kwargs* for a
                relative displacement.
            velocity: Move speed in mm/s.  Written to PLC parameter 103
                (global speed) before the move.
            timeout: Accepted for interface compatibility; not applied —
                the socket timeout on the underlying client provides the
                effective upper bound.
            **kwargs: Optional keyword overrides.  Recognised keys:

                - ``position_type`` (``str``): ``"absolute"`` (default) or
                  ``"relative"``.

        Returns:
            ``True`` when the PLC reports a successful move.

        Raises:
            ~applied_motion.backends.fposbapi_client.FPosBAPIClientError: If the
                PLC returns an error response.
        """
        position_type = kwargs.get("position_type", "absolute")
        rel_flag = 0 if position_type == "absolute" else 1
        logger.debug(
            "FPosBAxis '%s': move position=%s velocity=%s rel_flag=%d",
            self.name,
            position,
            velocity,
            rel_flag,
        )
        self._client.send_command("SET_PAR", 103, velocity)
        self._client.send_command("MOVE_AXIS", self.index, rel_flag, position)
        logger.info(
            "FPosBAxis '%s': move complete (position=%s mm, velocity=%s mm/s)",
            self.name,
            position,
            velocity,
        )
        return True

    def home(self) -> None:
        """Issue the ``HOME`` command, homing all axes together.

        The FPosBAPI ``HOME`` command references every axis simultaneously —
        there is no per-axis home.  Calling this method on any individual
        proxy triggers a full multi-axis homing sequence.

        .. note::
            :meth:`~applied_motion.gantry.Gantry.home` sends a single
            ``HOME`` command at the gantry level and does not call this method
            on each proxy individually, avoiding duplicate ``HOME`` requests.

        Raises:
            ~applied_motion.backends.fposbapi_client.FPosBAPIClientError: If the
                PLC returns an error response.
        """
        logger.info("FPosBAxis '%s': issuing HOME command (homes all axes)", self.name)
        self._client.send_command("HOME")

    def get_current_axis_position(self) -> float:
        """Return this axis's current position in mm via ``ROB_POS``.

        Sends ``ROB_POS`` and extracts this axis's coordinate from the
        response.  Assumes the response layout::

            msg_id, ROB_POS, x_mm, y_mm, z_mm, 0, NULL, SUCCESS

        where the position field for axis *n* is at ``fields[n + 1]``
        (1-based field numbering after stripping the leading ``msg_id``
        and ``ROB_POS`` label fields).

        Returns:
            Current axis position in mm.

        Raises:
            RuntimeError: If the response cannot be parsed.
            ~applied_motion.backends.fposbapi_client.FPosBAPIClientError: If the
                PLC returns an error response.
        """
        response = self._client.send_command("ROB_POS")
        fields = [
            f.strip() for f in response[-1].split(",")
        ]  # TODO: Make this a function in the client that is passed in here
        # fields[0] = msg_id, fields[1] = "ROB_POS", fields[2] = X, fields[3] = Y, fields[4] = Z
        position_field_index = self.index + 1
        try:
            value = float(fields[position_field_index])
        except (IndexError, ValueError) as exc:
            logger.error(
                "FPosBAxis '%s': cannot parse ROB_POS response — %s",
                self.name,
                response,
            )
            raise RuntimeError(
                f"Failed to parse ROB_POS position for axis '{self.name}' "
                f"(field index {position_field_index}): {response!r}"
            ) from exc
        logger.debug("FPosBAxis '%s': current position = %s mm", self.name, value)
        return value

    def is_homed(self) -> bool:
        """Return whether the gantry has been homed via ``IS_HOME``.

        Sends ``IS_HOME`` and interprets the response field that follows the
        echoed command name.  The PLC returns ``1`` when homing is complete
        and ``0`` when it is not.

        Returns:
            ``True`` if the PLC reports the gantry is homed; ``False``
            otherwise.

        Raises:
            ~applied_motion.backends.fposbapi_client.FPosBAPIClientError: If the
                PLC returns an error response.
            RuntimeError: If the response cannot be parsed.
        """
        response = self._client.send_command("IS_HOME")
        fields = [f.strip() for f in response[-1].split(",")]
        # fields: msg_id, IS_HOME, value, ERR_ID, ERR_TYPE, SUCCESS
        try:
            homed = bool(int(fields[2]))
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse IS_HOME response for axis '{self.name}': {response!r}") from exc
        logger.debug("FPosBAxis '%s': is_homed=%s", self.name, homed)
        return homed

    def stopped(self) -> bool:
        """Return ``True``; FPosBAPI moves are blocking so motion is always complete on return.

        :meth:`move` blocks until the PLC emits a ``SUCCESS`` response,
        meaning by the time Python regains control the axis has stopped.
        This method always returns ``True`` to satisfy the
        :class:`~applied_motion.backends.axis_protocol.Axis` interface.

        Returns:
            Always ``True``.
        """
        return True

    def ready_for_motion(self) -> bool:
        """Return whether the gantry drives are enabled via ``IS_ENBL``.

        Sends ``IS_ENBL`` and interprets the response.  The PLC returns
        ``1`` when drives are enabled and ready to accept motion commands,
        ``0`` when they are disabled.

        Returns:
            ``True`` if the PLC reports drives are enabled; ``False``
            otherwise.

        Raises:
            ~applied_motion.backends.fposbapi_client.FPosBAPIClientError: If the
                PLC returns an error response.
            RuntimeError: If the response cannot be parsed.
        """
        response = self._client.send_command("IS_ENBL")
        fields = [f.strip() for f in response[-1].split(",")]
        # fields: msg_id, IS_ENBL, value, ERR_ID, ERR_TYPE, SUCCESS
        try:
            enabled = bool(int(fields[2]))
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse IS_ENBL response for axis '{self.name}': {response!r}") from exc
        logger.debug("FPosBAxis '%s': ready_for_motion=%s", self.name, enabled)
        return enabled

    def __repr__(self) -> str:
        """Return an unambiguous string representation."""
        return f"FPosBAxis(name={self.name!r}, index={self.index!r})"

    def __str__(self) -> str:
        """Return a human-readable description."""
        return f"FPosBAxis '{self.name}' (axis {self.index})"

    def __eq__(self, other: object) -> bool:
        """Return ``True`` when *other* represents the same axis on the same controller.

        Args:
            other: Object to compare.

        Returns:
            ``True`` if *other* is an :class:`FPosBAxis` with equal
            *name* and *index*; ``NotImplemented`` otherwise.
        """
        if not isinstance(other, FPosBAxis):
            return NotImplemented
        return self.name == other.name and self.index == other.index

    def __hash__(self) -> int:
        """Return a hash derived from axis identity fields.

        Returns:
            Hash of ``(name, index)``.
        """
        return hash((self.name, self.index))
