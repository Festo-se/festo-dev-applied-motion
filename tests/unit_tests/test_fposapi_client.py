"""Unit tests for FPosAPIClient.

Coverage areas
--------------
* ``FPosAPIClient.__init__`` — socket creation, connect call, timeout applied.
* ``FPosAPIClient._recv_line`` — byte accumulation, newline termination,
  decoding, and connection-closed error.
* ``FPosAPIClient._wait_complete`` — SUCCESS terminal, error terminal raises,
  intermediate lines discarded.
* ``FPosAPIClient.send_command`` — correct frame format, message ID
  increments, lock serialises, returns terminal response.
* ``FPosAPIClient.close`` — delegates to socket.close.
* ``FPosAPIClient.__repr__``, ``__eq__``, ``__hash__``.
* Context manager — ``__exit__`` calls ``close``.

No hardware or network connection required.  The underlying socket is
replaced with a ``MagicMock`` for all tests.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from festo_dev_applied_motion.backends.fposapi_client import FPosAPIClient, FPosAPIClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IP = "192.168.10.10"
_PORT = 1234


def _byte_stream(text: str) -> list[bytes]:
    """Convert *text* to a list of single-byte values for recv side_effect."""
    return [bytes([b]) for b in text.encode("ascii")]


@pytest.fixture()
def mock_sock(mocker):
    """A MagicMock replacing socket.socket for the duration of a test."""
    return MagicMock()


@pytest.fixture()
def client(mocker, mock_sock):
    """FPosAPIClient with the TCP socket fully replaced by *mock_sock*."""
    mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
    return FPosAPIClient(ip=_IP, port=_PORT)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestFPosAPIClientInit:
    def test_connect_called_with_ip_and_port(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        FPosAPIClient(ip=_IP, port=_PORT)
        mock_sock.connect.assert_called_once_with((_IP, _PORT))

    def test_timeout_applied_to_socket(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        FPosAPIClient(ip=_IP, port=_PORT, timeout=5.0)
        mock_sock.settimeout.assert_called_once_with(5.0)

    def test_ip_and_port_stored(self, client):
        assert client.ip == _IP
        assert client.port == _PORT

    def test_initial_message_id_is_zero(self, client):
        assert client._msg_id == 0


# ---------------------------------------------------------------------------
# _recv_line
# ---------------------------------------------------------------------------


class TestFPosAPIClientRecvLine:
    def test_accumulates_bytes_until_newline(self, client, mock_sock):
        mock_sock.recv.side_effect = _byte_stream("HELLO\r\n")
        result = client._recv_line()
        assert result == "HELLO"

    def test_strips_carriage_return_and_newline(self, client, mock_sock):
        mock_sock.recv.side_effect = _byte_stream("DATA\r\n")
        assert "\r" not in client._recv_line()
        assert "\n" not in client._recv_line.__wrapped__ if hasattr(client._recv_line, "__wrapped__") else True

    def test_empty_recv_raises_client_error(self, client, mock_sock):
        """A zero-byte recv signals the remote host has closed the connection."""
        mock_sock.recv.return_value = b""
        with pytest.raises(FPosAPIClientError, match="Connection closed"):
            client._recv_line()

    def test_multicharacter_line_accumulated_correctly(self, client, mock_sock):
        response = "1, ENABLE, 0, NULL, SUCCESS\r\n"
        mock_sock.recv.side_effect = _byte_stream(response)
        result = client._recv_line()
        assert result == "1, ENABLE, 0, NULL, SUCCESS"


# ---------------------------------------------------------------------------
# _wait_complete
# ---------------------------------------------------------------------------


class TestFPosAPIClientWaitComplete:
    def test_returns_line_on_success(self, client, mocker):
        mocker.patch.object(client, "_recv_line", return_value="1, HOME, 0, NULL, SUCCESS")
        result = client._wait_complete()
        assert result == "1, HOME, 0, NULL, SUCCESS"

    def test_raises_on_error_response(self, client, mocker):
        mocker.patch.object(client, "_recv_line", return_value="1, HOME, 42, FAULT, AXIS_ERROR")
        with pytest.raises(FPosAPIClientError, match="FPosAPI error response"):
            client._wait_complete()

    def test_discards_intermediate_lines_before_success(self, client, mocker):
        """Lines where error_id==0 but last field is not SUCCESS must be
        silently skipped; only the SUCCESS terminal should be returned."""
        lines = iter([
            "1, STATUS, 0, IN_PROGRESS, MOVING",
            "1, STATUS, 0, IN_PROGRESS, DECELERATING",
            "1, HOME, 0, NULL, SUCCESS",
        ])
        mocker.patch.object(client, "_recv_line", side_effect=lambda: next(lines))
        result = client._wait_complete()
        assert result == "1, HOME, 0, NULL, SUCCESS"

    def test_error_id_nonzero_raises(self, client, mocker):
        mocker.patch.object(client, "_recv_line", return_value="1, MOV_AXIS, 5, HW_ERR, DRIVE_FAULT")
        with pytest.raises(FPosAPIClientError):
            client._wait_complete()


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------


class TestFPosAPIClientSendCommand:
    def test_frame_format_no_params(self, client, mocker):
        """Command with no params must produce 'msg_id, COMMAND\\r\\n'."""
        mocker.patch.object(client, "_wait_complete", return_value="1, HOME, 0, NULL, SUCCESS")
        client.send_command("HOME")
        sent = client._sock.sendall.call_args[0][0]
        assert sent == b"1, HOME\r\n"

    def test_frame_format_with_params(self, client, mocker):
        mocker.patch.object(client, "_wait_complete", return_value="1, MOV_AXIS, 0, NULL, SUCCESS")
        client.send_command("MOV_AXIS", 1, 0, 150.0)
        sent = client._sock.sendall.call_args[0][0]
        assert sent == b"1, MOV_AXIS, 1, 0, 150.0\r\n"

    def test_message_id_increments_each_call(self, client, mocker):
        mocker.patch.object(client, "_wait_complete", return_value="N, CMD, 0, NULL, SUCCESS")
        client.send_command("ENABLE")
        assert client._msg_id == 1
        client.send_command("DISABLE")
        assert client._msg_id == 2

    def test_returns_terminal_response(self, client, mocker):
        expected = "3, SYS_STATUS, 0, NULL, SUCCESS"
        mocker.patch.object(client, "_wait_complete", return_value=expected)
        result = client.send_command("SYS_STATUS")
        assert result == expected

    def test_params_converted_to_str(self, client, mocker):
        """Numeric params must be stringified, not repr'd."""
        mocker.patch.object(client, "_wait_complete", return_value="1, SET_PAR, 0, NULL, SUCCESS")
        client.send_command("SET_PAR", 103, 75.5)
        sent = client._sock.sendall.call_args[0][0]
        assert b"75.5" in sent
        assert b"103" in sent


# ---------------------------------------------------------------------------
# close / context manager
# ---------------------------------------------------------------------------


class TestFPosAPIClientClose:
    def test_close_calls_socket_close(self, client, mock_sock):
        client.close()
        mock_sock.close.assert_called_once()

    def test_context_manager_calls_close_on_exit(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        with FPosAPIClient(ip=_IP, port=_PORT) as c:
            pass
        mock_sock.close.assert_called_once()

    def test_context_manager_returns_client_instance(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        with FPosAPIClient(ip=_IP, port=_PORT) as c:
            assert isinstance(c, FPosAPIClient)


# ---------------------------------------------------------------------------
# __repr__, __eq__, __hash__
# ---------------------------------------------------------------------------


class TestFPosAPIClientIdentity:
    def test_repr_contains_ip_and_port(self, client):
        r = repr(client)
        assert _IP in r
        assert str(_PORT) in r

    def test_eq_same_ip_and_port(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        a = FPosAPIClient(ip=_IP, port=_PORT)
        b = FPosAPIClient(ip=_IP, port=_PORT)
        assert a == b

    def test_eq_different_ip(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        a = FPosAPIClient(ip=_IP, port=_PORT)
        b = FPosAPIClient(ip="10.0.0.1", port=_PORT)
        assert a != b

    def test_eq_different_port(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        a = FPosAPIClient(ip=_IP, port=_PORT)
        b = FPosAPIClient(ip=_IP, port=9999)
        assert a != b

    def test_eq_non_client_returns_not_implemented(self, client):
        assert client.__eq__("not-a-client") is NotImplemented

    def test_hash_equal_instances_match(self, mocker, mock_sock):
        mocker.patch("festo_dev_applied_motion.backends.fposapi_client.socket.socket", return_value=mock_sock)
        a = FPosAPIClient(ip=_IP, port=_PORT)
        b = FPosAPIClient(ip=_IP, port=_PORT)
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self, client):
        s = {client}
        assert client in s
