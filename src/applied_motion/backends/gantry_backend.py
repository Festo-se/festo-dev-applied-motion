# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Backend abstractions for gantry-level behavior.

This module centralizes backend-specific operations that are not purely
per-axis concerns (for example a single HOME command for all FPosB axes).
"""

from typing import Protocol, TypedDict

from applied_motion.backends.axis_protocol import Axis
from applied_motion.backends.fposbapi_client import FPosBAPIClient


class ControllerDiagnostics(TypedDict):
    """Controller diagnostics payload returned by gantry backends."""

    sys_status: str | None
    is_error: bool | None
    fpb_error: str | None
    read_err: str | None
    error: str | None


class GantryBackend(Protocol):
    """Protocol for backend-specific gantry actions.

    A backend may own shared resources (for example a TCP client) and can
    expose backend capabilities such as PLC teaching commands.
    """

    @property
    def client(self) -> FPosBAPIClient | None:
        """Return the shared FPosBAPI client if this backend uses one."""
        ...

    def backend_identity(self) -> tuple[type[object], tuple[str, int] | None]:
        """Return stable backend identity fields used for equality/hash."""
        ...

    def controller_diagnostics(self) -> ControllerDiagnostics | None:
        """Return controller diagnostics for status reporting, if available."""
        ...

    def home(self, axes: dict[str, Axis]) -> None:
        """Home all axes represented by *axes*."""
        ...

    def close(self) -> None:
        """Release backend-owned resources."""
        ...

    def supports_teach(self) -> bool:
        """Return whether TEACH_POS / TEACH_TRAY operations are supported."""
        ...

    def teach_pos(self, pos_id: int) -> None:
        """Commit current location into PLC position slot *pos_id*."""
        ...

    def teach_tray(self, tray_id: int, tray_pos: int) -> None:
        """Commit current location into PLC tray slot (*tray_id*, *tray_pos*)."""
        ...

    def list_commands(self) -> list[str]:
        """Return backend command list when available."""
        ...


class ModbusGantryBackend:
    """Gantry backend for direct Modbus axes.

    This backend owns no shared resources and uses per-axis operations only.
    """

    @property
    def client(self) -> FPosBAPIClient | None:
        """Modbus backend has no shared FPosBAPI client."""
        return None

    def backend_identity(self) -> tuple[type[object], tuple[str, int] | None]:
        """Return Modbus backend identity."""
        return type(self), None

    def controller_diagnostics(self) -> ControllerDiagnostics | None:
        """Modbus backend has no shared controller diagnostics."""
        return None

    def home(self, axes: dict[str, Axis]) -> None:
        """Home each axis sequentially."""
        for axis in axes.values():
            axis.home()

    def close(self) -> None:
        """Close backend resources.

        Modbus backend currently has no shared resources to close.
        """

    def supports_teach(self) -> bool:
        """Return whether teaching commands are supported."""
        return False

    def teach_pos(self, pos_id: int) -> None:
        """Raise for unsupported teaching operations."""
        raise NotImplementedError("teach_pos is only available for FPosBAPI backend")

    def teach_tray(self, tray_id: int, tray_pos: int) -> None:
        """Raise for unsupported teaching operations."""
        raise NotImplementedError("teach_tray is only available for FPosBAPI backend")

    def list_commands(self) -> list[str]:
        """Return an empty command list for unsupported operation."""
        return []


class FPosBAPIGantryBackend:
    """Gantry backend for PLC-controlled FPosBAPI systems."""

    def __init__(self, client: FPosBAPIClient, owns_client: bool = True) -> None:
        """Store the shared client.

        Args:
            client: Shared PLC TCP client.
            owns_client: When ``True``, :meth:`close` closes *client*.
        """
        self._client = client
        self._owns_client = owns_client
        self._closed = False

    @property
    def client(self) -> FPosBAPIClient | None:
        """Return the shared FPosBAPI client."""
        return self._client

    def backend_identity(self) -> tuple[type[object], tuple[str, int] | None]:
        """Return FPosBAPI backend identity including endpoint."""
        return type(self), (self._client.ip, self._client.port)

    def controller_diagnostics(self) -> ControllerDiagnostics | None:
        """Return controller diagnostics from shared client."""
        try:
            return {
                "sys_status": self._client.sys_status(),
                "is_error": self._client.is_error(),
                "fpb_error": self._client.fpb_error(),
                "read_err": self._client.read_err(),
                "error": None,
            }
        except Exception as exc:
            return {
                "sys_status": None,
                "is_error": None,
                "fpb_error": None,
                "read_err": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def home(self, axes: dict[str, Axis]) -> None:
        """Home all axes through one PLC HOME command."""
        self._client.send_command("HOME")

    def close(self) -> None:
        """Close owned shared client exactly once."""
        if self._closed or not self._owns_client:
            return
        self._client.close()
        self._closed = True

    def supports_teach(self) -> bool:
        """Return whether teaching commands are supported."""
        return True

    def teach_pos(self, pos_id: int) -> None:
        """Delegate TEACH_POS to shared client."""
        self._client.teach_pos(pos_id=pos_id)

    def teach_tray(self, tray_id: int, tray_pos: int) -> None:
        """Delegate TEACH_TRAY to shared client."""
        self._client.teach_tray(tray_id=tray_id, tray_pos=tray_pos)

    def list_commands(self) -> list[str]:
        """Return command list from the shared client."""
        return self._client.list_commands()
