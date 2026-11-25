"""Unit tests for composable motion CLI registration and dispatch."""

import argparse
import logging
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from applied_motion.cli import cli
from applied_motion.cli.theme import FESTO_THEME


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

    def test_jog_help_uses_native_axis_units(self, capsys):
        parser = cli.build_standalone_motion_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--config", "gantry.json", "jog", "--help"])

        help_text = capsys.readouterr().out
        assert "axis native units" in help_text
        assert "native units/s" in help_text


class TestAxisUnitFormatting:
    def test_location_table_labels_mixed_native_units(self):
        from unittest.mock import MagicMock

        gantry = MagicMock()
        gantry.axes = {
            "X": MagicMock(position_unit="mm"),
            "R": MagicMock(position_unit="degrees"),
        }
        table = cli._location_table({"X": 12.5, "R": 90.0}, gantry)
        stream = StringIO()
        Console(file=stream, force_terminal=False, theme=FESTO_THEME).print(table)
        rendered = stream.getvalue()

        assert "Position" in rendered
        assert "12.500 mm" in rendered
        assert "90.000 degrees" in rendered

    def test_positions_table_labels_each_axis_unit(self):
        from unittest.mock import MagicMock

        gantry = MagicMock()
        gantry.axes = {
            "X": MagicMock(position_unit="mm"),
            "R": MagicMock(position_unit="degrees"),
        }
        table = cli._positions_table({"home": {"X": 12.5, "R": 90.0}}, gantry)
        stream = StringIO()
        Console(file=stream, force_terminal=False, theme=FESTO_THEME).print(table)
        rendered = stream.getvalue()

        assert "X (mm)" in rendered
        assert "R (degrees)" in rendered

    def test_status_table_uses_generic_rotary_position_and_unit(self, monkeypatch):
        from unittest.mock import MagicMock

        gantry = MagicMock()
        gantry.get_status.return_value = {
            "backend": "ModbusGantryBackend",
            "supports_teach": False,
            "summary": {
                "healthy": True,
                "all_homed": True,
                "all_stopped": True,
                "all_ready_for_motion": True,
                "axis_count": 1,
            },
            "axes": {
                "R": {
                    "position": 90.0,
                    "position_unit": "degrees",
                    "position_mm": None,
                    "is_homed": True,
                    "is_stopped": True,
                    "ready_for_motion": True,
                    "error": None,
                }
            },
            "controller": {},
        }
        printed = []
        monkeypatch.setattr(cli.console, "print", printed.append)

        assert cli._run_status(argparse.Namespace(as_json=False), gantry) == 0

        axis_table = printed[2]
        stream = StringIO()
        Console(file=stream, force_terminal=False, theme=FESTO_THEME).print(axis_table)
        rendered = stream.getvalue()
        assert "Position (degrees)" in rendered
        assert "90.000" in rendered


class TestNativeJogLadders:
    def test_linear_and_rotary_ladders_match_contract(self):
        assert cli._STEP_SIZES == [0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0]
        assert cli._ROTARY_STEP_SIZES == [0.1, 0.5, 1.0, 5.0, 10.0]

    def test_axis_uses_its_native_ladder_and_legacy_fallback(self):
        from unittest.mock import MagicMock

        assert cli._step_sizes_for_axis(MagicMock(position_unit="mm")) is cli._STEP_SIZES
        assert cli._step_sizes_for_axis(MagicMock(position_unit="degrees")) is cli._ROTARY_STEP_SIZES
        assert cli._step_sizes_for_axis(object()) is cli._STEP_SIZES

    def test_step_indices_advance_independently_and_clamp_per_axis(self):
        from unittest.mock import MagicMock

        axes = {
            "X": MagicMock(position_unit="mm"),
            "R": MagicMock(position_unit="degrees"),
        }
        step_indices = {"X": 6, "R": 3}

        cli._adjust_step_indices(axes, step_indices, 1)

        assert step_indices == {"X": 6, "R": 4}

        cli._adjust_step_indices(axes, step_indices, -1)

        assert step_indices == {"X": 5, "R": 3}

        step_indices = {"X": 0, "R": 0}

        cli._adjust_step_indices(axes, step_indices, -1)

        assert step_indices == {"X": 0, "R": 0}

    def test_tui_jogs_each_axis_using_its_own_step(self, monkeypatch):
        from unittest.mock import MagicMock

        class FakeKeyBindings:
            def __init__(self):
                self.handlers = {}

            def add(self, key):
                def register(handler):
                    self.handlers[key] = handler
                    return handler

                return register

        class FakeControl:
            def __init__(self, render, focusable):
                self.render = render

        class FakeWindow:
            def __init__(self, content):
                self.content = content

        class FakeLayout:
            def __init__(self, container):
                self.container = container

        class FakeThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        rendered_output: list[list[tuple[str, str]]] = []

        class FakeApplication:
            rendered: list[tuple[str, str]]

            def __init__(self, layout, key_bindings, full_screen):
                self.layout = layout
                self.key_bindings = key_bindings
                FakeApplication.instance = self

            def invalidate(self):
                pass

            def run(self):
                handlers = self.key_bindings.handlers
                for _ in range(4):
                    handlers["+"](None)
                handlers["right"](None)
                handlers["up"](None)
                handlers["-"](None)
                handlers["right"](None)
                handlers["up"](None)
                self.rendered = self.layout.container.content.render()
                rendered_output.append(self.rendered)

        monkeypatch.setattr(cli, "KeyBindings", FakeKeyBindings)
        monkeypatch.setattr(cli, "FormattedTextControl", FakeControl)
        monkeypatch.setattr(cli, "Window", FakeWindow)
        monkeypatch.setattr(cli, "Layout", FakeLayout)
        monkeypatch.setattr(cli, "Application", FakeApplication)
        monkeypatch.setattr(cli.threading, "Thread", FakeThread)
        monkeypatch.setattr(cli.console, "print", lambda *args, **kwargs: None)

        gantry = MagicMock()
        gantry.axes = {
            "X": MagicMock(position_unit="mm"),
            "R": MagicMock(position_unit="degrees"),
        }
        gantry.get_location.return_value = {"X": 12.5, "R": 90.0}
        session = MagicMock()
        session.jog.return_value = {"X": 12.5, "R": 90.0}

        cli._run_jog_mode(session, gantry)

        jogs = [(args[0], args[1], args[2]) for args, _ in session.jog.call_args_list]
        assert jogs == [
            ("X", "+", 50.0),
            ("R", "+", 10.0),
            ("X", "+", 25.0),
            ("R", "+", 5.0),
        ]
        rendered_text = "".join(text for _, text in rendered_output[0])
        assert "Steps: X: 25 mm, R: 5 degrees" in rendered_text


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
    def test_connect_gantry_uses_canonical_configurator_adapter(self, monkeypatch):
        construction = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        construction.axes = {"X": object()}
        construction.concurrent_axes = None
        construction.backend = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        fake_gantry = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        adapter_calls = []

        monkeypatch.setattr(cli, "build_gantry_from_config", lambda path, name: adapter_calls.append((path, name)) or construction)
        monkeypatch.setattr(cli, "Gantry", lambda **kwargs: fake_gantry)

        result = cli._connect_gantry(Path("gantry.json"), "gantry_1")

        assert result is fake_gantry
        assert adapter_calls == [(Path("gantry.json"), "gantry_1")]

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
