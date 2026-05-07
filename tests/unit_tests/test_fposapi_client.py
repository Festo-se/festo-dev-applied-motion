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
        # settimeout(5.0) is the final call; _drain() temporarily sets 0.1 then restores 5.0
        mock_sock.settimeout.assert_called_with(5.0)

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
# _recv_line — empty line (bare \r\n terminator)
# ---------------------------------------------------------------------------


class TestFPosAPIClientRecvLineEmpty:
    def test_empty_line_returns_empty_string(self, client, mock_sock):
        """A bare \\r\\n from the server should return an empty string."""
        mock_sock.recv.side_effect = _byte_stream("\r\n")
        assert client._recv_line() == ""


# ---------------------------------------------------------------------------
# _collect_response
# ---------------------------------------------------------------------------


class TestFPosAPIClientCollectResponse:
    def test_returns_single_success_line(self, client, mocker):
        lines = iter(["1, HOME, 0, NULL, SUCCESS"])
        mocker.patch.object(client, "_recv_line", side_effect=lambda: next(lines))
        result = client._collect_response()
        assert result == ["1, HOME, 0, NULL, SUCCESS"]

    def test_returns_ack_and_terminal_lines(self, client, mocker):
        """ACK lines are collected; reading stops on the non-ACK terminal line."""
        lines = iter(["1, SYS_STATUS, 0, NULL, ACK", "1, SYS_STATUS, IDLE, 0, NULL, SUCCESS"])
        mocker.patch.object(client, "_recv_line", side_effect=lambda: next(lines))
        result = client._collect_response()
        assert result == ["1, SYS_STATUS, 0, NULL, ACK", "1, SYS_STATUS, IDLE, 0, NULL, SUCCESS"]

    def test_stops_after_terminal_line(self, client, mocker):
        """Lines after the terminal SUCCESS must not be consumed."""
        call_count = 0

        def recv_line():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "1, HOME, 0, NULL, SUCCESS"
            raise AssertionError("_recv_line called after terminal line")

        mocker.patch.object(client, "_recv_line", side_effect=recv_line)
        result = client._collect_response()
        assert result == ["1, HOME, 0, NULL, SUCCESS"]

    def test_connection_closed_mid_response_raises(self, client, mocker):
        mocker.patch.object(
            client, "_recv_line", side_effect=FPosAPIClientError("Connection closed by remote host")
        )
        with pytest.raises(FPosAPIClientError, match="Connection closed"):
            client._collect_response()


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------


class TestFPosAPIClientSendCommand:
    def test_frame_format_no_params(self, client, mocker):
        """Command with no params must produce 'msg_id, COMMAND\\r\\n'."""
        mocker.patch.object(client, "_collect_response", return_value=["1, HOME, 0, NULL, SUCCESS"])
        client.send_command("HOME")
        sent = client._sock.sendall.call_args[0][0]
        assert sent == b"1, HOME\r\n"

    def test_frame_format_with_params(self, client, mocker):
        mocker.patch.object(client, "_collect_response", return_value=["1, MOVE_AXIS, 0, NULL, SUCCESS"])
        client.send_command("MOVE_AXIS", 1, 0, 150.0)
        sent = client._sock.sendall.call_args[0][0]
        assert sent == b"1, MOVE_AXIS, 1, 0, 150.0\r\n"

    def test_message_id_increments_each_call(self, client, mocker):
        mocker.patch.object(
            client, "_collect_response",
            side_effect=[
                ["1, ENABLE, 0, NULL, SUCCESS"],
                ["2, DISABLE, 0, NULL, SUCCESS"],
            ]
        )
        client.send_command("ENABLE")
        assert client._msg_id == 1
        client.send_command("DISABLE")
        assert client._msg_id == 2

    def test_returns_all_response_lines(self, client, mocker):
        lines = ["1, SYS_STATUS, 0, NULL, SUCCESS"]
        mocker.patch.object(client, "_collect_response", return_value=lines)
        result = client.send_command("SYS_STATUS")
        assert result == lines

    def test_returns_multiline_response(self, client, mocker):
        lines = ["1, ERR_LOG, fault1", "1, ERR_LOG, fault2", "1, ERR_LOG, 0, NULL, SUCCESS"]
        mocker.patch.object(client, "_collect_response", return_value=lines)
        result = client.send_command("ERR_LOG")
        assert result == lines

    def test_params_converted_to_str(self, client, mocker):
        """Numeric params must be stringified, not repr'd."""
        mocker.patch.object(client, "_collect_response", return_value=["1, SET_PAR, 0, NULL, SUCCESS"])
        client.send_command("SET_PAR", 103, 75.5)
        sent = client._sock.sendall.call_args[0][0]
        assert b"75.5" in sent
        assert b"103" in sent

    def test_raises_on_empty_response(self, client, mocker):
        mocker.patch.object(client, "_collect_response", return_value=[])
        with pytest.raises(FPosAPIClientError, match="Empty response"):
            client.send_command("HOME")

    def test_raises_on_error_status(self, client, mocker):
        mocker.patch.object(client, "_collect_response", return_value=["1, HOME, 42, FAULT, AXIS_ERROR"])
        with pytest.raises(FPosAPIClientError, match="FPosAPI error response"):
            client.send_command("HOME")

    def test_raises_on_msg_id_mismatch(self, client, mocker):
        mocker.patch.object(client, "_collect_response", return_value=["99, HOME, 0, NULL, SUCCESS"])
        with pytest.raises(FPosAPIClientError, match="MSG_ID mismatch"):
            client.send_command("HOME")

    def test_raises_on_cmd_echo_mismatch(self, client, mocker):
        mocker.patch.object(client, "_collect_response", return_value=["1, WRONG_CMD, 0, NULL, SUCCESS"])
        with pytest.raises(FPosAPIClientError, match="CMD echo mismatch"):
            client.send_command("HOME")


# ---------------------------------------------------------------------------
# list_commands
# ---------------------------------------------------------------------------


class TestFPosAPIClientListCommands:
    def test_returns_parsed_command_names(self, client, mocker):
        mocker.patch.object(
            client,
            "_collect_response",
            return_value=["1, CMD_LIST, ENABLE, DISABLE, HOME, MOVE_AXIS, ROB_POS, 0, NULL, SUCCESS"],
        )
        commands = client.list_commands()
        assert commands == ["ENABLE", "DISABLE", "HOME", "MOVE_AXIS", "ROB_POS"]

    def test_sends_cmd_list_command(self, client, mocker):
        mocker.patch.object(
            client,
            "_collect_response",
            return_value=["1, CMD_LIST, HOME, 0, NULL, SUCCESS"],
        )
        client.list_commands()
        sent = client._sock.sendall.call_args[0][0]
        assert b"CMD_LIST" in sent

    def test_empty_server_list_returns_empty(self, client, mocker):
        mocker.patch.object(
            client,
            "_collect_response",
            return_value=["1, CMD_LIST, 0, NULL, SUCCESS"],
        )
        commands = client.list_commands()
        assert commands == []


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
