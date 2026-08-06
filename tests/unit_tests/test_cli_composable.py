"""Unit tests for composable motion CLI registration and dispatch."""

import argparse
from pathlib import Path

import pytest

from applied_motion.cli import cli


class TestBuildStandaloneMotionParser:
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
    def test_can_mount_under_parent_parser(self):
        parent = argparse.ArgumentParser(prog="system")
        top = parent.add_subparsers(dest="domain")

        cli.register_motion_cli(top)

        args = parent.parse_args(["motion", "--config", "gantry.json", "status"])

        assert args.domain == "motion"
        assert args.motion_command == "status"
        assert callable(args._handler)

    def test_extension_can_add_nested_subcommand(self):
        parent = argparse.ArgumentParser(prog="system")
        top = parent.add_subparsers(dest="domain")

        def extension(motion_subparsers):
            parser = motion_subparsers.add_parser("extra")
            parser.set_defaults(_handler=lambda _: 9)

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
        fake_gantry.__enter__ = lambda s: fake_gantry
        fake_gantry.__exit__ = lambda s, *a: None

        parser = cli.build_standalone_motion_parser()
        args = parser.parse_args(["--config", "gantry.json", "jog-tui"])
        cli.dispatch_motion_command(args)

        assert len(jog_mode_calls) == 1


class TestDispatchMotionCommand:
    def test_raises_when_handler_missing(self):
        with pytest.raises(ValueError, match="No motion command selected"):
            cli.dispatch_motion_command(argparse.Namespace())


class TestMain:
    def test_defaults_to_shell_when_subcommand_missing(self, monkeypatch):
        called = {"count": 0}

        def fake_shell(args):
            called["count"] += 1
            assert isinstance(args.config, Path)
            return 0

        monkeypatch.setattr(cli, "_run_shell", fake_shell)

        cli.main(["--config", "gantry.json"])

        assert called["count"] == 1
