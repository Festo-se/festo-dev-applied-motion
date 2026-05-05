# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


r"""Thread-safe ASCII TCP socket client for the Festo FPosAPI server.

The FPosAPI server runs on the Festo CECC-X PLC (CoDeSys) and listens on
TCP port 1234.  It speaks a line-oriented ASCII protocol::

    Request:  MSG_ID, COMMAND[, PARAM, ...]\r\n
    Response: MSG_ID, COMMAND[, ECHO_PARAMS..., RETURN_VALS...], ERR_ID, ERR_TYPE, ERR_MSG\r\n

On success the last three comma-delimited fields are ``0, NULL, SUCCESS``.
On error the last three fields carry a non-zero error id, type string, and
message string.

A single :class:`FPosAPIClient` instance should be shared across all
:class:`~festo_dev_applied_motion.backends.fposapi_axis.FPosAxis` objects belonging
to the same gantry, and is owned by :class:`~festo_dev_applied_motion.gantry.Gantry`.
All send/receive operations are serialised through an internal
:class:`threading.Lock`.
"""

import logging
import socket
import threading

logger = logging.getLogger(__name__)


class FPosAPIClientError(Exception):
    """Raised when the FPosAPI server returns an error response or the connection is lost."""


class FPosAPIClient:
    """Thread-safe TCP socket client for the Festo FPosAPI ASCII protocol.

    Connects to the CECC-X PLC on *ip*:*port* and wraps the request/response
    cycle in :meth:`send_command`.  A :class:`threading.Lock` serialises all
    socket I/O so multiple :class:`~festo_dev_applied_motion.backends.fposapi_axis.FPosAxis`
    objects sharing the same client do not interleave their frames.

    Attributes:
        ip: IPv4 address of the CECC-X TCP server.
        port: TCP port of the FPosAPI server (default ``1234``).
    """

    def __init__(self, ip: str, port: int = 1234, timeout: float = 0.0) -> None:
        """Connect to the FPosAPI server.

        Args:
            ip: IPv4 address of the CECC-X PLC.
            port: TCP port the FPosAPI server is listening on.  Defaults to
                ``1234``.
            timeout: Socket timeout in seconds.  Applied to both ``connect``
                and ``recv`` operations.  Defaults to ``0.0``.

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
        self._sock.settimeout(timeout)
        self._sock.connect((ip, port))
        logger.info("FPosAPIClient connected to %s:%d", ip, port)

    def _next_id(self) -> int:
        """Return the next monotonically increasing message ID.

        Returns:
            Integer message ID for the next command frame.
        """
        self._msg_id += 1
        return self._msg_id

    def send_command(self, command: str, *params) -> str:
        """Send a command to the FPosAPI server and return the final response line.

        Acquires the internal lock, increments the message ID, formats the
        ASCII request frame, sends it, then loops on :meth:`_recv_line` until
        the response satisfies the success or error terminal condition.

        Args:
            command: FPosAPI command string, e.g. ``"ENABLE"``, ``"MOV_AXIS"``.
            *params: Zero or more positional parameters appended to the frame.

        Returns:
            The full response line string (including echoed command and error
            tuple) when the server reports ``SUCCESS``.

        Raises:
            FPosAPIClientError: If the server returns a non-zero error id, or
                if the connection is closed before a terminal response arrives.
        """
        with self._lock:
            msg_id = self._next_id()
            parts = [str(msg_id), command] + [str(p) for p in params]
            raw = ", ".join(parts) + "\r\n"
            logger.debug("FPosAPIClient → %s", raw.strip())
            self._sock.sendall(raw.encode("ascii"))
            return self._wait_complete()

    def _recv_line(self) -> str:
        r"""Read exactly one ``\r\n``-terminated line from the socket.

        Reads one byte at a time until ``\\n`` is received, then decodes and
        strips the result.  Safe to call from within :meth:`send_command`
        while the lock is held.

        Returns:
            Stripped ASCII line string (no trailing ``\r\n``).

        Raises:
            FPosAPIClientError: If the remote host closes the connection
                (``recv`` returns ``b""``).
        """
        buf = b""
        while True:
            ch = self._sock.recv(1)
            if not ch:
                raise FPosAPIClientError("Connection closed by remote host")
            buf += ch
            if ch == b"\n":
                line = buf.decode("ascii").strip()
                logger.debug("FPosAPIClient ← %s", line)
                return line

    def _wait_complete(self) -> str:
        """Loop on :meth:`_recv_line` until the terminal success or error line.

        The FPosAPI server may emit intermediate status lines before the final
        response.  This method discards intermediate lines and returns only
        the terminal line.

        Returns:
            Terminal response line with ``SUCCESS`` as the last field.

        Raises:
            FPosAPIClientError: If the server reports a non-zero error id.
        """
        while True:
            line = self._recv_line()
            fields = [f.strip() for f in line.split(",")]
            if len(fields) >= 3:
                if fields[-1] == "SUCCESS":
                    return line
                if fields[-3] != "0":
                    raise FPosAPIClientError(f"FPosAPI error response: {line}")

    def close(self) -> None:
        """Close the underlying TCP socket.

        Safe to call more than once; subsequent calls are no-ops because the
        OS will already have released the socket descriptor.
        """
        self._sock.close()
        logger.info("FPosAPIClient disconnected from %s:%d", self.ip, self.port)

    def __enter__(self) -> "FPosAPIClient":
        """Return *self* to support use as a context manager."""
        return self

    def __exit__(self, *args) -> None:
        """Close the socket on context manager exit."""
        self.close()

    def __repr__(self) -> str:
        """Return an unambiguous string representation."""
        return f"FPosAPIClient(ip={self.ip!r}, port={self.port!r})"

    def __eq__(self, other: object) -> bool:
        """Return ``True`` when *other* targets the same server endpoint.

        Args:
            other: Object to compare.

        Returns:
            ``True`` if *other* is an :class:`FPosAPIClient` with equal *ip*
            and *port*; ``False`` otherwise.
        """
        if not isinstance(other, FPosAPIClient):
            return NotImplemented
        return self.ip == other.ip and self.port == other.port

    def __hash__(self) -> int:
        """Return a hash derived from the server endpoint.

        Returns:
            Hash of ``(ip, port)``.
        """
        return hash((self.ip, self.port))
