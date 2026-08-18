"""Unit tests for composable motion CLI registration and dispatch."""

import argparse
import logging
from pathlib import Path

import pytest

from applied_motion.cli import cli


class TestBuildStandaloneMotionParser:
    def test_log_level_defaults_to_off(self):
        parser = cli.build_standalone_motion_parser()

        args = parser.parse_args(["--config", "gantry.json", "where"])

        assert args.log_level == "OFF"

    def test_where_command_sets_handler(self):
        parser = cli.build_standalone_motion_parser()

        args = parser.parse_args(["--config", "gantry.json", "where"])

        assert args.motion_command == "where"
        assert callable(args._handler)

    def test_jog_command_parses_runtime_args(self):
        parser = cli.build_standalone_motion_parser()

        args = parser.parse_args(["--config", "gantry.json", "jog", "x", "+", "5", "--velocity", "20"])

        assert args.axis == "x"
        assert args.direction == "+"
        assert args.step == 5.0
        assert args.velocity == 20.0


class TestRegisterMotionCli:
    def test_nested_motion_cli_defaults_log_level_to_inherit(self):
        parent = argparse.ArgumentParser(prog="system")
        top = parent.add_subparsers(dest="domain")

        cli.register_motion_cli(top)

        args = parent.parse_args(["motion", "--config", "gantry.json", "status"])

        assert args.log_level == "INHERIT"

    def test_can_mount_under_parent_parser(self):
        parent = argparse.ArgumentParser(prog="system")
        top = parent.add_subparsers(dest="domain")

        cli.register_motion_cli(top)

        args = parent.parse_args(["motion", "--config", "gantry.json", "status"])

        assert args.domain == "motion"
        assert args.motion_command == "status"
        assert callable(args._handler)

    def test_extension_can_add_nested_subcommand(self, monkeypatch):
        parent = argparse.ArgumentParser(prog="system")
        top = parent.add_subparsers(dest="domain")

        fake_gantry = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(cli, "_connect_gantry", lambda *_: fake_gantry)

        def extension(motion_subparsers):
            parser = motion_subparsers.add_parser("extra")
            parser.set_defaults(_handler=lambda args, gantry: 9)

        cli.register_motion_cli(top, extensions=[extension])
        args = parent.parse_args(["motion", "--config", "gantry.json", "extra"])

        assert args.motion_command == "extra"
        assert cli.dispatch_motion_command(args) == 9


class TestJogTuiSubcommand:
    def test_jog_tui_sets_handler(self):
        parser = cli.build_standalone_motion_parser()
        args = parser.parse_args(["--config", "gantry.json", "jog-tui"])
        assert args.motion_command == "jog-tui"
        assert callable(args._handler)

    def test_jog_tui_calls_run_jog_mode(self, monkeypatch):
        jog_mode_calls = []

        def fake_jog_mode(session, gantry):
            jog_mode_calls.append((session, gantry))

        monkeypatch.setattr(cli, "_run_jog_mode", fake_jog_mode)

        fake_gantry = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(cli, "_connect_gantry", lambda *_: fake_gantry)

        parser = cli.build_standalone_motion_parser()
        args = parser.parse_args(["--config", "gantry.json", "jog-tui"])
        cli.dispatch_motion_command(args)

        assert len(jog_mode_calls) == 1


class TestDispatchMotionCommand:
    def test_configure_logging_disables_logging_when_off(self, monkeypatch):
        from applied_motion.cli import logging_utils

        disable_calls: list[int] = []
        basic_config_called = {"value": False}

        monkeypatch.setattr(logging_utils.logging, "disable", disable_calls.append)
        monkeypatch.setattr(
            logging_utils.logging,
            "basicConfig",
            lambda **kwargs: basic_config_called.__setitem__("value", True),
        )

        assert logging_utils.configure_logging("OFF") == "OFF"

        assert disable_calls == [logging_utils.logging.CRITICAL]
        assert basic_config_called["value"] is False

    def test_configure_logging_enables_requested_level(self, monkeypatch):
        from applied_motion.cli import logging_utils

        disable_calls: list[int] = []
        basic_config_calls: list[dict[str, object]] = []

        monkeypatch.setattr(logging_utils.logging, "disable", disable_calls.append)
        monkeypatch.setattr(logging_utils.logging, "basicConfig", lambda **kwargs: basic_config_calls.append(kwargs))

        assert logging_utils.configure_logging("INFO") == "INFO"

        assert disable_calls == [logging_utils.logging.NOTSET]
        assert basic_config_calls == [
            {
                "level": logging_utils.logging.INFO,
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "force": True,
                "stream": logging_utils.sys.stdout,
            }
        ]

    def test_configure_logging_inherit_does_not_reconfigure(self, monkeypatch):
        from applied_motion.cli import logging_utils

        disable_calls: list[int] = []
        basic_config_calls: list[dict[str, object]] = []

        monkeypatch.setattr(logging_utils, "current_log_level_name", lambda: "INFO")
        monkeypatch.setattr(logging_utils, "_has_parent_logging_configuration", lambda: True)
        monkeypatch.setattr(logging_utils.logging, "disable", disable_calls.append)
        monkeypatch.setattr(logging_utils.logging, "basicConfig", lambda **kwargs: basic_config_calls.append(kwargs))

        assert logging_utils.configure_logging("INHERIT") == "INFO"

        assert disable_calls == []
        assert basic_config_calls == []

    def test_configure_logging_inherit_falls_back_to_off_without_parent_logging(self, monkeypatch):
        from applied_motion.cli import logging_utils

        disable_calls: list[int] = []

        monkeypatch.setattr(logging_utils, "_has_parent_logging_configuration", lambda: False)
        monkeypatch.setattr(logging_utils.logging, "disable", disable_calls.append)
        monkeypatch.setattr(logging_utils, "_configure_pymodbus_logger", lambda: None)

        assert logging_utils.configure_logging("INHERIT") == "OFF"
        assert disable_calls == [logging_utils.logging.CRITICAL]

    def test_runtime_log_level_command_sets_requested_level(self, monkeypatch):
        from applied_motion.cli import logging_utils

        monkeypatch.setattr(logging_utils, "configure_logging", lambda level: level)

        assert logging_utils.set_runtime_log_level(["debug"]) == "Log level set to DEBUG"

    def test_runtime_log_level_command_reports_current_level(self, monkeypatch):
        from applied_motion.cli import logging_utils

        monkeypatch.setattr(logging_utils, "current_log_level_name", lambda: "WARNING")

        assert logging_utils.set_runtime_log_level([]) == "Current log level: WARNING"

    def test_configure_logging_forces_pymodbus_logger_to_info(self, monkeypatch):
        from applied_motion.cli import logging_utils

        monkeypatch.setattr(logging_utils.logging, "disable", lambda _: None)
        monkeypatch.setattr(logging_utils.logging, "basicConfig", lambda **kwargs: None)
        monkeypatch.setattr(logging_utils, "_PYMODBUS_LOGGER_NAME", "pymodbus.applied-motion.test")
        monkeypatch.setattr(logging_utils.logging.root.manager, "disable", logging.NOTSET)

        pymodbus_logger = logging.getLogger("pymodbus.applied-motion.test")
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        pymodbus_logger.handlers = [handler]
        pymodbus_logger.setLevel(logging.DEBUG)

        assert logging_utils.configure_logging("DEBUG") == "DEBUG"
        assert pymodbus_logger.level == logging.INFO
        assert pymodbus_logger.handlers[0].level == logging.INFO

        pymodbus_logger.handlers.clear()

    def test_configure_logging_off_disables_pymodbus_logger(self, monkeypatch):
        from applied_motion.cli import logging_utils

        monkeypatch.setattr(logging_utils, "_PYMODBUS_LOGGER_NAME", "pymodbus.applied-motion.off-test")
        monkeypatch.setattr(logging_utils.logging.root.manager, "disable", logging.NOTSET)

        pymodbus_logger = logging.getLogger("pymodbus.applied-motion.off-test")
        pymodbus_logger.disabled = False

        assert logging_utils.configure_logging("OFF") == "OFF"
        assert pymodbus_logger.disabled is True
        logging.disable(logging.NOTSET)

    def test_raises_when_handler_missing(self):
        with pytest.raises(ValueError, match="No motion command selected"):
            cli.dispatch_motion_command(argparse.Namespace())


class TestMain:
    def test_defaults_to_shell_when_subcommand_missing(self, monkeypatch):
        called = {"count": 0}

        def fake_shell(args, gantry):
            called["count"] += 1
            assert isinstance(args.config, Path)
            return 0

        monkeypatch.setattr(cli, "_run_shell", fake_shell)
        fake_gantry = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(cli, "_connect_gantry", lambda *_: fake_gantry)

        cli.main(["--config", "gantry.json"])

        assert called["count"] == 1


class TestInteractiveOutputStability:
    def test_run_repl_uses_patch_stdout(self, monkeypatch):
        patch_calls: list[str] = []

        class _PromptSessionStub:
            def __init__(self, **kwargs):
                del kwargs

            def prompt(self, prompt_text: str) -> str:
                del prompt_text
                return "quit"

        class _PatchStdoutStub:
            def __enter__(self):
                patch_calls.append("enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                patch_calls.append("exit")
                del exc_type, exc, tb
                return False

        fake_gantry = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        fake_gantry.axes = {}

        monkeypatch.setattr(cli, "PromptSession", _PromptSessionStub)
        monkeypatch.setattr(cli.console, "print", lambda *args, **kwargs: None)

        exit_code = cli.run_repl(__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(), fake_gantry)

        assert exit_code == 1
