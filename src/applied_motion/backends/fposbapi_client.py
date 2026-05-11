# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


r"""Thread-safe ASCII TCP socket client for the Festo FPosBAPI server.

The FPosBAPI server runs on the Festo CECC-X PLC (CoDeSys) and listens on
TCP port 1234.  It speaks a line-oriented ASCII protocol::

    Request:  MSG_ID, COMMAND[, PARAM, ...]\r\n
    Response: MSG_ID, COMMAND[, ECHO_PARAMS..., RETURN_VALS...], ERR_ID, ERR_TYPE, ERR_MSG\r\n

On success the last three comma-delimited fields are ``0, NULL, SUCCESS``.
On error the last three fields carry a non-zero error id, type string, and
message string.

A single :class:`FPosBAPIClient` instance should be shared across all
:class:`~applied_motion.backends.fposbapi_axis.FPosBAxis` objects belonging
to the same gantry, and is owned by :class:`~applied_motion.gantry.Gantry`.
All send/receive operations are serialised through an internal
:class:`threading.Lock`.
"""

import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)


class FPosBAPIClientError(Exception):
    """Raised when the FPosBAPI server returns an error response or the connection is lost."""


class FPosBAPIClient:
    """Thread-safe TCP socket client for the Festo FPosBAPI ASCII protocol.

    Connects to the CECC-X PLC on *ip*:*port* and wraps the request/response
    cycle in :meth:`send_command`.  A :class:`threading.Lock` serialises all
    socket I/O so multiple :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis`
    objects sharing the same client do not interleave their frames.

    Attributes:
        ip: IPv4 address of the CECC-X TCP server.
        port: TCP port of the FPosBAPI server (default ``1234``).
    """

    def __init__(self, ip: str, port: int = 1234, timeout: float | None = None) -> None:
        """Connect to the FPosBAPI server.

        Args:
            ip: IPv4 address of the CECC-X PLC.
            port: TCP port the FPosBAPI server is listening on.  Defaults to
                ``1234``.
            timeout: Socket timeout in seconds.  Applied to both ``connect``
                and ``recv`` operations.  ``None`` (default) means blocking
                with no timeout.

        Raises:
            OSError: If the TCP connection cannot be established within
                *timeout* seconds.
        """
        self.ip = ip
        self.port = port
        self._timeout = timeout
        self._lock = threading.Lock()
        self._msg_id = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._connect()
        logger.info("FPosBAPIClient connected to %s:%d", ip, port)

    def _configure_keepalive(self, sock: socket.socket) -> None:
        """Enable and tune TCP keepalive where supported."""
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        # Windows-specific keepalive tuning:
        # (onoff, keepalivetime_ms, keepaliveinterval_ms)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15_000, 5_000))

    def _connect(self) -> None:
        """Open and configure a TCP socket to the configured endpoint."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        self._configure_keepalive(sock)
        sock.connect((self.ip, self.port))
        self._sock = sock
        self._drain()

    def _reconnect(self) -> None:
        """Close the current socket and establish a fresh connection."""
        logger.warning("FPosBAPIClient: reconnecting to %s:%d", self.ip, self.port)
        try:
            self._sock.close()
        except OSError:
            pass
        self._connect()
        logger.info("FPosBAPIClient: reconnected to %s:%d", self.ip, self.port)

    def _drain(self, max_chunks: int = 64) -> None:
        r"""Discard bytes buffered in the receive socket.

        The CECC-X PLC may deliver queued response bytes from a previous TCP
        session to new connections immediately after ``connect()``, or may
        append a bare ``\\r\\n`` frame terminator after error responses.  This
        method sets a short non-blocking timeout, reads until the socket goes
        quiet or *max_chunks* have been consumed, then restores the
        caller-configured timeout.

        Args:
            max_chunks: Upper bound on ``recv`` calls, so that mock sockets
                (which never raise ``socket.timeout``) exit cleanly in tests.
        """
        self._sock.settimeout(0.1)
        try:
            for _ in range(max_chunks):
                if not self._sock.recv(4096):
                    break
        except (socket.timeout, BlockingIOError, OSError):  # noqa
            pass
        finally:
            self._sock.settimeout(self._timeout)
        logger.debug("FPosBAPIClient: socket drained (%d chunk max)", max_chunks)

    def _next_id(self) -> int:
        """Return the next monotonically increasing message ID.

        Returns:
            Integer message ID for the next command frame.
        """
        self._msg_id += 1
        return self._msg_id

    def send_command(self, command: str, *params) -> list[str]:
        """Send a command to the FPosBAPI server and return all response lines.

        Acquires the internal lock, increments the message ID, formats the
        ASCII request frame, sends it, then reads lines via
        :meth:`_collect_response` until the terminal (non-``ACK``) line
        arrives.  The terminal (last) line is validated for correct MSG_ID
        echo, CMD echo, and ``SUCCESS`` status before returning.

        Args:
            command: FPosBAPI command string, e.g. ``"ENABLE"``, ``"MOVE_AXIS"``.
            *params: Zero or more positional parameters appended to the frame.

        Returns:
            All response lines received before the empty terminator, in arrival
            order.  The last element is always the terminal status line echoing
            the command and carrying ``SUCCESS``.

        Raises:
            FPosBAPIClientError: If the server returns an error status, the
                echoed MSG_ID or command name does not match what was sent, the
                response is empty, or the connection is closed.
        """
        with self._lock:
            msg_id = self._next_id()
            parts = [str(msg_id), command] + [str(p) for p in params]
            raw = ", ".join(parts) + "\r\n"
            logger.debug("FPosBAPIClient <- %s", raw.strip())
            try:
                self._sock.sendall(raw.encode("ascii"))
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                logger.warning("FPosBAPIClient send failed (%s); reconnecting once", exc)
                try:
                    self._reconnect()
                    self._sock.sendall(raw.encode("ascii"))
                except OSError as retry_exc:
                    raise FPosBAPIClientError(f"Connection lost during {command!r}: {retry_exc}") from retry_exc
            lines = self._collect_response()

            # Validate while holding the lock so subsequent send_command calls
            # see a clean socket regardless of the outcome here.
            if not lines:
                self._drain(max_chunks=1)
                raise FPosBAPIClientError(f"Empty response to {command!r}")

            terminal = lines[-1]
            fields = [f.strip() for f in terminal.split(",")]

            if "VEAB" in command:
                fields = self._handle_malformed_veab_output(fields, msg_id, command)
                logger.debug("FPosBAPIClient <- %s", ",".join(fields))
            if len(fields) < 3:
                raise FPosBAPIClientError(f"Malformed response to {command!r}: {terminal!r}")
            if fields[0] != str(msg_id):
                raise FPosBAPIClientError(f"MSG_ID mismatch: sent {msg_id}, got {fields[0]!r} in {terminal!r}")
            if fields[1] != command:
                raise FPosBAPIClientError(f"CMD echo mismatch: sent {command!r}, got {fields[1]!r} in {terminal!r}")
            if fields[-1] != "SUCCESS":
                raise FPosBAPIClientError(f"FPosBAPI error response: {terminal}")

        return lines

    def _handle_malformed_veab_output(self, fields: list[str], msg_id: int, command: str):
        """Fix malformed VEAB hook output.

        The VEAB hook on the controller has an API non-conformant output, but is
        technically correct and not indicative of an error state. This function rectifies this.
        Total hack
        """
        fields[0] = str(msg_id)
        fields[1] = command

        return fields

    def get_veab(self) -> int:
        """Get the pressure set value of the VEAB."""
        # TODO: This __REALLY__ needs to be contingent on the config and how it is passed in
        # TODO: The ideal would be a PressureControl() class that shares the intantiated gantry and can take it in as input parameter. All of these classes would benefit from that bc of necessary shared socket on CECC-X

        lines = self.send_command("GET_VEAB")
        terminal = lines[-1]
        fields = [f.strip() for f in terminal.split(",")]

        return int(fields[2])

    def set_veab(self, set_value: int) -> None:
        """Set the pressure value of the VEAB."""
        self.send_command("SET_VEAB", set_value)
        # TODO: add polling loop to get_veab() to sample this until the value is set and bail after a timeout

    def _recv_line(self) -> str:
        r"""Read exactly one ``\r\n``-terminated line from the socket.

        Reads one byte at a time until ``\\n`` is received, then decodes and
        strips the result.  An empty string is returned for a bare ``\r\n``
        terminator line.

        Returns:
            Stripped ASCII line string (no trailing ``\r\n``).  Empty string
            for a bare terminator.

        Raises:
            FPosBAPIClientError: If the remote host closes the connection
                (``recv`` returns ``b""``).
        """
        buf = b""
        while True:
            try:
                ch = self._sock.recv(1)
            except OSError as exc:
                raise FPosBAPIClientError(f"Connection lost: {exc}") from exc
            if not ch:
                raise FPosBAPIClientError("Connection closed by remote host")
            buf += ch
            if ch == b"\n":
                line = buf.decode("ascii").strip()
                logger.debug("FPosBAPIClient <- %r", line)
                return line

    def _collect_response(self) -> list[str]:
        r"""Read response lines from the socket until the terminal line arrives.

        Calls :meth:`_recv_line` repeatedly.  Each response frame begins with
        zero or more intermediate ``ACK`` lines (last comma-delimited field is
        ``ACK``) followed by a single terminal line whose last field is either
        ``SUCCESS`` or an error string.  Reading stops as soon as a non-``ACK``
        line is received (or an empty line, treated defensively as end-of-frame).

        Returns:
            List of stripped response lines in arrival order, including the
            terminal line.

        Raises:
            FPosBAPIClientError: If the connection is closed before the terminal
                line arrives.
        """
        lines: list[str] = []
        while True:
            line = self._recv_line()
            if line:
                lines.append(line)
            last_field = line.split(",")[-1].strip() if line else ""
            if last_field != "ACK":
                break
        # The PLC appends a bare \r\n frame terminator after error responses
        # (but NOT after success responses).  Consume it while still holding
        # the caller's lock to prevent it from corrupting the next command.
        if lines:
            terminal_last = lines[-1].split(",")[-1].strip()
            if terminal_last not in ("ACK", "SUCCESS"):
                self._sock.settimeout(0.5)
                try:
                    self._recv_line()  # discard the bare \r\n terminator
                except (socket.timeout, OSError):  # noqa
                    pass  # PLC did not send a frame terminator — that is fine
                finally:
                    self._sock.settimeout(self._timeout)
        return lines

    def list_commands(self) -> list[str]:
        """Query the server for its supported command set via ``CMD_LIST``.

        Sends ``CMD_LIST`` and parses the response to extract the command
        names the connected FPosBAPI server advertises.  The set varies
        between firmware versions, so callers can use this to feature-detect
        before issuing optional commands (e.g. ``IS_HOME``, ``SYS_STATUS``).

        Response layout assumed::

            MSG_ID, CMD_LIST, CMD_1, CMD_2, ..., CMD_N, ERR_ID, ERR_TYPE, ERR_MSG

        The first two fields (``MSG_ID`` and ``CMD_LIST``) and the last
        three (error fields ending with ``SUCCESS``) are stripped; the
        remaining fields are returned as a list of command name strings.

        Returns:
            List of command name strings advertised by the server,
            e.g. ``["ENABLE", "DISABLE", "HOME", "MOVE_AXIS", ...]``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("CMD_LIST")
        terminal = lines[-1]
        fields = [f.strip() for f in terminal.split(",")]
        # Strip: fields[0]=msg_id, fields[1]="CMD_LIST"; trailing: ERR_ID, ERR_TYPE, "SUCCESS"
        command_fields = fields[2:-3]
        commands = [c for c in command_fields if c]
        logger.debug("CMD_LIST returned %d command(s): %s", len(commands), commands)
        return commands

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Enable the gantry drives via ``ENABLE``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("ENABLE")
        logger.info("FPosBAPIClient: drives enabled")

    def disable(self) -> None:
        """Disable the gantry drives via ``DISABLE``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("DISABLE")
        logger.info("FPosBAPIClient: drives disabled")

    def home(self) -> None:
        """Home all axes via ``HOME``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("HOME")
        logger.info("FPosBAPIClient: homing complete")

    def move_pos(
        self,
        pos_id: int,
        tool_id: int = 0,
        retract_z: int = 0,
        slow_app: int = 0,
    ) -> None:
        """Execute a motion sequence to a stored position via ``MOVE_POS``.

        Args:
            pos_id: Position record ID (1-100).
            tool_id: Tool index to apply.  ``0`` selects no tool.
            retract_z: ``1`` to retract Z before moving, ``0`` to skip.
            slow_app: ``1`` to slow approach the target position, ``0`` for
                full speed.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("MOVE_POS", pos_id, tool_id, retract_z, slow_app)
        logger.info("FPosBAPIClient: MOVE_POS complete (pos_id=%d, tool_id=%d)", pos_id, tool_id)

    def move_loc(
        self,
        a1: float,
        a2: float,
        a3: float,
        tool_id: int = 0,
        move_typ: int = 0,
        retract_z: int = 0,
        slow_app: int = 0,
    ) -> None:
        """Execute a coordinated 3-axis move to explicit coordinates via ``MOVE_LOC``.

        Args:
            a1: Target X-axis position in mm.
            a2: Target Y-axis position in mm.
            a3: Target Z-axis position in mm.
            tool_id: Tool offset to apply.  ``0`` selects no tool.
            move_typ: ``0`` for absolute, ``1`` for relative.
            retract_z: ``1`` to retract Z before moving, ``0`` to skip.
            slow_app: ``1`` to slow approach the target position, ``0`` for
                full speed.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("MOVE_LOC", a1, a2, a3, tool_id, move_typ, retract_z, slow_app)
        logger.info("FPosBAPIClient: MOVE_LOC complete (a1=%s, a2=%s, a3=%s mm)", a1, a2, a3)

    def move_path(self) -> None:
        """Execute the programmed motion path via ``MOVE_PATH``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        # TODO: Needs WRITE_PATH?
        self.send_command("MOVE_PATH")
        logger.info("FPosBAPIClient: MOVE_PATH complete")

    def halt(self) -> None:
        """Halt the current motion via ``HALT``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("HALT")
        logger.info("FPosBAPIClient: HALT issued")

    def resume(self) -> None:
        """Resume a halted motion via ``RESUME``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("RESUME")
        logger.info("FPosBAPIClient: RESUME issued")

    def abort(self) -> None:
        """Abort the current motion via ``ABORT``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("ABORT")
        logger.info("FPosBAPIClient: ABORT issued")

    def reset_err(self) -> None:
        """Reset the active error via ``RESET_ERR``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("RESET_ERR")
        logger.info("FPosBAPIClient: error reset")

    def open_valve(
        self,
        v1_time: int = 0,
        v2_time: int = 0,
        v3_time: int = 0,
        v4_time: int = 0,
        v5_time: int = 0,
        v6_time: int = 0,
        v7_time: int = 0,
        v8_time: int = 0,
    ) -> None:
        """Open one or more valves for the specified durations via ``OPEN_VALVE``.

        Args:
            v1_time: Valve 1 open duration in ms.  ``0`` keeps the valve closed.
            v2_time: Valve 2 open duration in ms.
            v3_time: Valve 3 open duration in ms.
            v4_time: Valve 4 open duration in ms.
            v5_time: Valve 5 open duration in ms.
            v6_time: Valve 6 open duration in ms.
            v7_time: Valve 7 open duration in ms.
            v8_time: Valve 8 open duration in ms.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command(
            "OPEN_VALVE",
            v1_time,
            v2_time,
            v3_time,
            v4_time,
            v5_time,
            v6_time,
            v7_time,
            v8_time,
        )
        logger.info("FPosBAPIClient: OPEN_VALVE complete")

    # ------------------------------------------------------------------
    # Teaching commands
    # ------------------------------------------------------------------

    def read_pos(self, pos_id: int) -> tuple[float, float, float]:
        """Read the stored absolute position for *pos_id* via ``READ_POS``.

        Response layout::

            MSG_ID, READ_POS, POS_ID, ABS_A1, ABS_A2, ABS_A3, 0, NULL, SUCCESS

        Args:
            pos_id: Position record ID (1-100).

        Returns:
            Tuple of ``(abs_a1, abs_a2, abs_a3)`` in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("READ_POS", pos_id)
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, READ_POS, pos_id, abs_a1, abs_a2, abs_a3, 0, NULL, SUCCESS
        try:
            result = float(fields[3]), float(fields[4]), float(fields[5])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse READ_POS response for pos_id={pos_id}: {lines!r}") from exc
        logger.debug("FPosBAPIClient: READ_POS pos_id=%d → %s", pos_id, result)
        return result

    def teach_pos(self, pos_id: int, tool_id: int = 0) -> None:
        """Save the current axis positions to *pos_id* via ``TEACH_POS``.

        Args:
            pos_id: Position record ID to write (1-100).
            tool_id: Tool offset to associate with this position.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("TEACH_POS", pos_id, tool_id)
        logger.info("FPosBAPIClient: TEACH_POS complete (pos_id=%d)", pos_id)

    def write_pos(
        self,
        pos_id: int,
        abs_a1: float,
        abs_a2: float,
        abs_a3: float,
    ) -> None:
        """Write absolute axis coordinates to *pos_id* via ``WRITE_POS``.

        Args:
            pos_id: Position record ID to write (1-100).
            abs_a1: Absolute X-axis position in mm.
            abs_a2: Absolute Y-axis position in mm.
            abs_a3: Absolute Z-axis position in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("WRITE_POS", pos_id, abs_a1, abs_a2, abs_a3)
        logger.info(
            "FPosBAPIClient: WRITE_POS complete (pos_id=%d, a1=%s, a2=%s, a3=%s mm)",
            pos_id,
            abs_a1,
            abs_a2,
            abs_a3,
        )

    def mod_pos(
        self,
        pos_id: int,
        rel_a1: float,
        rel_a2: float,
        rel_a3: float,
    ) -> None:
        """Modify a stored position by relative offsets via ``MOD_POS``.

        Args:
            pos_id: Position record ID to modify (1-100).
            rel_a1: Relative X-axis offset in mm.
            rel_a2: Relative Y-axis offset in mm.
            rel_a3: Relative Z-axis offset in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("MOD_POS", pos_id, rel_a1, rel_a2, rel_a3)
        logger.info("FPosBAPIClient: MOD_POS complete (pos_id=%d)", pos_id)

    def write_path(
        self,
        pa_pos_id: int,
        abs_a1: float,
        abs_a2: float,
        abs_a3: float,
    ) -> None:
        """Write one waypoint along the motion path via ``WRITE_PATH``.

        Args:
            pa_pos_id: Path position index (1-10).
            abs_a1: Absolute X-axis position in mm.
            abs_a2: Absolute Y-axis position in mm.
            abs_a3: Absolute Z-axis position in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("WRITE_PATH", pa_pos_id, abs_a1, abs_a2, abs_a3)
        logger.info("FPosBAPIClient: WRITE_PATH complete (pa_pos_id=%d)", pa_pos_id)

    def read_path(self, pa_pos_id: int) -> tuple[float, float, float]:
        """Read one waypoint from the motion path via ``READ_PATH``.

        Args:
            pa_pos_id: Path position index (1-10).

        Returns:
            Tuple of ``(abs_a1, abs_a2, abs_a3)`` in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("READ_PATH", pa_pos_id)
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, READ_PATH, pa_pos_id, abs_a1, abs_a2, abs_a3, 0, NULL, SUCCESS
        try:
            result = float(fields[3]), float(fields[4]), float(fields[5])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse READ_PATH response for pa_pos_id={pa_pos_id}: {lines!r}") from exc
        logger.debug("FPosBAPIClient: READ_PATH pa_pos_id=%d → %s", pa_pos_id, result)
        return result

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def sys_status(self) -> str:
        """Return the system status string via ``SYS_STATUS``.

        Response layout::

            MSG_ID, SYS_STATUS, STATUS, 0, NULL, SUCCESS

        Returns:
            Status string reported by the PLC (e.g. ``"IDLE"``).

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("SYS_STATUS")
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, SYS_STATUS, status, 0, NULL, SUCCESS
        try:
            status = fields[2]
        except IndexError as exc:
            raise RuntimeError(f"Failed to parse SYS_STATUS response: {lines!r}") from exc
        logger.debug("FPosBAPIClient: SYS_STATUS → %r", status)
        return status

    def is_error(self) -> bool:
        """Return whether the gantry is in an error state via ``IS_ERROR``.

        Response layout::

            MSG_ID, IS_ERROR, FLAG, 0, NULL, SUCCESS

        Returns:
            ``True`` if the PLC reports an active error; ``False`` otherwise.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("IS_ERROR")
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, IS_ERROR, flag, 0, NULL, SUCCESS
        try:
            result = bool(int(fields[2]))
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse IS_ERROR response: {lines!r}") from exc
        logger.debug("FPosBAPIClient: IS_ERROR → %s", result)
        return result

    def fpb_error(self) -> str:
        """Return the fieldbus error state via ``FPB_ERROR``.

        Response layout::

            MSG_ID, FPB_ERROR, STATUS, 0, NULL, SUCCESS

        Returns:
            Raw status string from the terminal response field.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("FPB_ERROR")
        fields = [f.strip() for f in lines[-1].split(",")]
        status = fields[2] if len(fields) > 2 else ""
        logger.debug("FPosBAPIClient: FPB_ERROR → %r", status)
        return status

    def read_err(self) -> str:
        """Return the current error description via ``READ_ERR``.

        Returns:
            The raw terminal response line from the server.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("READ_ERR")
        logger.debug("FPosBAPIClient: READ_ERR → %r", lines[-1])
        return lines[-1]

    def err_log(self) -> list[str]:
        """Return the error log entries via ``ERR_LOG``.

        The server may return multiple intermediate lines before the terminal
        SUCCESS line.  All non-terminal lines are returned as the log.

        Returns:
            List of error log strings (all lines except the terminal status).

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("ERR_LOG")
        log = lines[:-1]  # strip terminal SUCCESS line
        logger.debug("FPosBAPIClient: ERR_LOG returned %d line(s)", len(log))
        return log

    def com_log(self) -> list[str]:
        """Return the communication log entries via ``COM_LOG``.

        The server may return multiple intermediate lines before the terminal
        SUCCESS line.  All non-terminal lines are returned as the log.

        Returns:
            List of communication log strings (all lines except the terminal status).

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("COM_LOG")
        log = lines[:-1]  # strip terminal SUCCESS line
        logger.debug("FPosBAPIClient: COM_LOG returned %d line(s)", len(log))
        return log

    def err_desc(self) -> str:
        """Return the error description via ``ERR_DESC``.

        Returns:
            Raw terminal response line from the server.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("ERR_DESC")
        logger.debug("FPosBAPIClient: ERR_DESC → %r", lines[-1])
        return lines[-1]

    # ------------------------------------------------------------------
    # I/O and parameters
    # ------------------------------------------------------------------

    def get_io(self, io_id: int) -> float:
        """Return the value of an I/O channel via ``GET_IO``.

        Response layout::

            MSG_ID, GET_IO, IO_ID, VALUE, 0, NULL, SUCCESS

        Args:
            io_id: I/O channel ID.

        Returns:
            Current value of the I/O channel.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("GET_IO", io_id)
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, GET_IO, io_id, value, 0, NULL, SUCCESS
        try:
            value = float(fields[3])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Failed to parse GET_IO response for io_id={io_id}: {lines!r}") from exc
        logger.debug("FPosBAPIClient: GET_IO io_id=%d → %s", io_id, value)
        return value

    def set_io(self, io_id: int, value: float) -> None:
        """Set the value of an I/O channel via ``SET_IO``.

        Args:
            io_id: I/O channel ID.
            value: Value to write to the I/O channel.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("SET_IO", io_id, value)
        logger.info("FPosBAPIClient: SET_IO io_id=%d value=%s", io_id, value)

    def get_par(self, par_id: int) -> list[float]:
        """Return parameter values for *par_id* via ``GET_PAR``.

        The FPosBAPI server returns up to four value fields per parameter
        (e.g. tray parameters include column count, column offset, row count,
        and row offset).  Fields that are absent or non-numeric are omitted
        from the returned list.

        Response layout::

            MSG_ID, GET_PAR, PAR_ID, VALUE, VALUE, VALUE, VALUE, 0, NULL, SUCCESS

        Args:
            par_id: Parameter ID (see API Rev 5 Parameter ID sheet).

        Returns:
            List of up to four float values for the requested parameter.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        lines = self.send_command("GET_PAR", par_id)
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, GET_PAR, par_id, v1, v2, v3, v4, 0, NULL, SUCCESS
        # Value fields are at indices 3-6; trailing error triplet at -3 to -1.
        value_fields = fields[3:-3]
        values: list[float] = []
        for f in value_fields:
            try:
                values.append(float(f))
            except ValueError:
                break
        logger.debug("FPosBAPIClient: GET_PAR par_id=%d → %s", par_id, values)
        return values

    def set_par(self, par_id: int, *values: float) -> None:
        """Write one or more values to parameter *par_id* via ``SET_PAR``.

        Args:
            par_id: Parameter ID (see API Rev 5 Parameter ID sheet).
            *values: Up to four values to write to the parameter.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("SET_PAR", par_id, *values)
        logger.info("FPosBAPIClient: SET_PAR par_id=%d values=%s", par_id, values)

    # ------------------------------------------------------------------
    # Tray commands
    # ------------------------------------------------------------------

    def teach_tray(self, tray_id: int, tray_pos: int, tool_id: int = 0) -> None:
        """Save the current axis positions as a tray position via ``TEACH_TRAY``.

        Args:
            tray_id: Tray ID (1-20).
            tray_pos: Position within the tray to teach.
            tool_id: Tool offset to associate with this position.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("TEACH_TRAY", tray_id, tray_pos, tool_id)
        logger.info("FPosBAPIClient: TEACH_TRAY complete (tray_id=%d, tray_pos=%d)", tray_id, tray_pos)

    def write_tray(
        self,
        tray_id: int,
        tray_pos: int,
        abs_a1: float,
        abs_a2: float,
        abs_a3: float,
    ) -> None:
        """Write absolute coordinates to a tray position via ``WRITE_TRAY``.

        Args:
            tray_id: Tray ID (1-20).
            tray_pos: Position index within the tray.
            abs_a1: Absolute X-axis position in mm.
            abs_a2: Absolute Y-axis position in mm.
            abs_a3: Absolute Z-axis position in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("WRITE_TRAY", tray_id, tray_pos, abs_a1, abs_a2, abs_a3)
        logger.info("FPosBAPIClient: WRITE_TRAY complete (tray_id=%d, tray_pos=%d)", tray_id, tray_pos)

    def read_tray(self, tray_id: int, tray_pos: int) -> tuple[float, float, float]:
        """Read the stored coordinates of a tray position via ``READ_TRAY``.

        Response layout::

            MSG_ID, READ_TRAY, TRAY_ID, TRAY_POS, ABS_A1, ABS_A2, ABS_A3, 0, NULL, SUCCESS

        Args:
            tray_id: Tray ID (1-20).
            tray_pos: Position index within the tray.

        Returns:
            Tuple of ``(abs_a1, abs_a2, abs_a3)`` in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
            RuntimeError: If the response cannot be parsed.
        """
        lines = self.send_command("READ_TRAY", tray_id, tray_pos)
        fields = [f.strip() for f in lines[-1].split(",")]
        # fields: msg_id, READ_TRAY, tray_id, tray_pos, abs_a1, abs_a2, abs_a3, 0, NULL, SUCCESS
        try:
            result = float(fields[4]), float(fields[5]), float(fields[6])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"Failed to parse READ_TRAY response for tray_id={tray_id}, tray_pos={tray_pos}: {lines!r}"
            ) from exc
        logger.debug("FPosBAPIClient: READ_TRAY tray_id=%d tray_pos=%d → %s", tray_id, tray_pos, result)
        return result

    def mod_tray(
        self,
        tray_id: int,
        tray_pos: int,
        rel_a1: float,
        rel_a2: float,
        rel_a3: float,
    ) -> None:
        """Modify a stored tray position by relative offsets via ``MOD_TRAY``.

        Args:
            tray_id: Tray ID (1-20).
            tray_pos: Position index within the tray.
            rel_a1: Relative X-axis offset in mm.
            rel_a2: Relative Y-axis offset in mm.
            rel_a3: Relative Z-axis offset in mm.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("MOD_TRAY", tray_id, tray_pos, rel_a1, rel_a2, rel_a3)
        logger.info("FPosBAPIClient: MOD_TRAY complete (tray_id=%d, tray_pos=%d)", tray_id, tray_pos)

    def move_tray(
        self,
        tray_id: int,
        tray_col: int,
        tray_row: int,
        tool_id: int = 0,
        retract_z: int = 0,
        slow_app: int = 0,
    ) -> None:
        """Move the gantry to a tray location via ``MOVE_TRAY``.

        Args:
            tray_id: Tray ID (1-20).
            tray_col: Target column within the tray.
            tray_row: Target row within the tray.
            tool_id: Tool offset to apply.  ``0`` selects no tool.
            retract_z: ``1`` to retract Z before moving, ``0`` to skip.
            slow_app: ``1`` to slow approach the target position, ``0`` for
                full speed.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("MOVE_TRAY", tray_id, tray_col, tray_row, tool_id, retract_z, slow_app)
        logger.info(
            "FPosBAPIClient: MOVE_TRAY complete (tray_id=%d, col=%d, row=%d)",
            tray_id,
            tray_col,
            tray_row,
        )

    # ------------------------------------------------------------------
    # Custom / system commands
    # ------------------------------------------------------------------

    def init_sys(self) -> None:
        """Initialize system parameters via ``INIT_SYS``.

        Raises:
            FPosBAPIClientError: If the server returns an error response or
                the connection is lost.
        """
        self.send_command("INIT_SYS")
        logger.info("FPosBAPIClient: system initialized")

    def close(self) -> None:
        """Close the underlying TCP socket.

        Safe to call more than once; subsequent calls are no-ops because the
        OS will already have released the socket descriptor.
        """
        self._sock.close()
        logger.info("FPosBAPIClient disconnected from %s:%d", self.ip, self.port)

    # def reconnect(self) -> None:
    #     """Close the current socket and open a fresh connection to the same server.

    #     Call this after a :class:`FPosBAPIClientError` caused by a PLC reboot
    #     or a dropped TCP connection.  The new socket is drained of any
    #     buffered bytes before this method returns.

    #     Raises:
    #         OSError: If the TCP connection cannot be re-established.
    #     """
    #     logger.info("FPosBAPIClient: reconnecting to %s:%d", self.ip, self.port)
    #     try:
    #         self._sock.close()
    #     except OSError:
    #         pass
    #     self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #     self._sock.settimeout(self._timeout)
    #     self._sock.connect((self.ip, self.port))
    #     self._drain()
    #     logger.info("FPosBAPIClient: reconnected to %s:%d", self.ip, self.port)

    # def wait_until_ready(
    #     self,
    #     deadline: float = 120.0,
    #     poll_interval: float = 2.0,
    #     probe_timeout: float = 5.0,
    # ) -> None:
    #     """Block until the FPosBAPI server responds to ``CMD_LIST`` or raise on timeout.

    #     The CECC-X PLC opens the TCP port before the CoDeSys FPosBAPI runtime
    #     is ready to service commands.  This method polls with a short per-attempt
    #     socket timeout rather than a single long blocking call, so it fails fast
    #     on each attempt and retries until the server becomes responsive or
    #     *deadline* seconds elapse.

    #     Typical CECC-X boot time is 30-90 seconds.  The default *deadline* of
    #     120 s gives the PLC ample time without waiting indefinitely. # TODO: Verify this

    #     Args:
    #         deadline: Maximum total seconds to wait.  Raises
    #             :class:`FPosBAPIClientError` if the server is still not ready.
    #             Defaults to ``120.0``.
    #         poll_interval: Seconds to wait between probe attempts.  Defaults
    #             to ``2.0``.
    #         probe_timeout: Per-attempt socket timeout in seconds.  Kept short
    #             so unresponsive attempts fail quickly.  Defaults to ``5.0``.

    #     Raises:
    #         FPosBAPIClientError: If the server has not responded within *deadline*
    #             seconds.
    #     """
    #     end = time.monotonic() + deadline
    #     attempt = 0
    #     logger.info(
    #         "FPosBAPIClient: waiting up to %.0fs for server at %s:%d to become ready",
    #         deadline,
    #         self.ip,
    #         self.port,
    #     )
    #     while True:
    #         attempt += 1
    #         # Use a disposable socket per attempt — never touch self._sock,
    #         # which may be in use by another thread via send_command.
    #         probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    #         try:
    #             probe_sock.settimeout(probe_timeout)
    #             probe_sock.connect((self.ip, self.port))
    #             msg_id = 1
    #             probe_sock.sendall(f"{msg_id}, CMD_LIST\r\n".encode("ascii"))
    #             buf = b""
    #             while b"\n" not in buf:
    #                 chunk = probe_sock.recv(4096)
    #                 if not chunk:
    #                     raise OSError("Connection closed")
    #                 buf += chunk
    #             logger.info(
    #                 "FPosBAPIClient: server ready after attempt %d",
    #                 attempt,
    #             )
    #             return
    #         except OSError as exc:
    #             remaining = end - time.monotonic()
    #             if remaining <= 0:
    #                 raise FPosBAPIClientError(
    #                     f"Server at {self.ip}:{self.port} not ready after {deadline:.0f}s"
    #                 ) from exc
    #             logger.debug(
    #                 "FPosBAPIClient: attempt %d failed (%s), %.0fs remaining",
    #                 attempt,
    #                 exc,
    #                 remaining,
    #             )
    #             time.sleep(min(poll_interval, remaining))
    #         finally:
    #             probe_sock.close()

    def __enter__(self) -> "FPosBAPIClient":
        """Return *self* to support use as a context manager."""
        return self

    def __exit__(self, *args) -> None:
        """Close the socket on context manager exit."""
        self.close()

    def __repr__(self) -> str:
        """Return an unambiguous string representation."""
        return f"FPosBAPIClient(ip={self.ip!r}, port={self.port!r})"

    def __eq__(self, other: object) -> bool:
        """Return ``True`` when *other* targets the same server endpoint.

        Args:
            other: Object to compare.

        Returns:
            ``True`` if *other* is an :class:`FPosBAPIClient` with equal *ip*
            and *port*; ``False`` otherwise.
        """
        if not isinstance(other, FPosBAPIClient):
            return NotImplemented
        return self.ip == other.ip and self.port == other.port

    def __hash__(self) -> int:
        """Return a hash derived from the server endpoint.

        Returns:
            Hash of ``(ip, port)``.
        """
        return hash((self.ip, self.port))
