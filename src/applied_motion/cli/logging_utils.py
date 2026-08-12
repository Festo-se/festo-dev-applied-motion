# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Runtime logging helpers for applied-motion CLI entry points.

These helpers support two related use cases:

* configuring logging once at process startup for standalone and composed CLIs,
* changing the active root log level while an interactive CLI is already
  running.
"""

import logging
import sys
from collections.abc import Sequence

LOG_LEVEL_CHOICES: list[str] = ["OFF", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
INHERITED_LOG_LEVEL_CHOICES: list[str] = ["INHERIT", *LOG_LEVEL_CHOICES]
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_PYMODBUS_LOGGER_NAME = "pymodbus"
_PYMODBUS_LOG_LEVEL = logging.INFO


def _has_parent_logging_configuration() -> bool:
    """Return whether process logging appears configured by caller.

    Returns:
        ``True`` when root logging has already been configured or globally
        disabled by a parent CLI. ``False`` when logging still appears to be in
        Python's untouched default state.
    """
    root_logger = logging.getLogger()
    return bool(root_logger.handlers) or logging.root.manager.disable != logging.NOTSET


def _configure_pymodbus_logger() -> None:
    """Force PyModbus logger to use INFO threshold when logging enabled.

    PyModbus can become extremely noisy when root logging runs at ``DEBUG``.
    This helper keeps its logger pinned to ``INFO`` regardless of the broader
    CLI log-level setting. When process logging is globally disabled, PyModbus
    logging is disabled too.
    """
    pymodbus_logger = logging.getLogger(_PYMODBUS_LOGGER_NAME)
    if logging.root.manager.disable >= logging.CRITICAL:
        pymodbus_logger.disabled = True
        return

    pymodbus_logger.disabled = False
    pymodbus_logger.setLevel(_PYMODBUS_LOG_LEVEL)
    for handler in pymodbus_logger.handlers:
        handler.setLevel(_PYMODBUS_LOG_LEVEL)


def current_log_level_name() -> str:
    """Return current effective root log level name.

    Returns:
        Effective root log level name, or ``"OFF"`` when global logging is
        disabled.
    """
    if logging.root.manager.disable >= logging.CRITICAL:
        return "OFF"

    level_name = logging.getLevelName(logging.getLogger().getEffectiveLevel())
    return level_name if isinstance(level_name, str) else str(level_name)


def configure_logging(log_level: str | None) -> str:
    """Configure or inherit process logging.

    Args:
        log_level: Requested log level name. ``None`` or ``"INHERIT"`` keeps
            current logging configuration unchanged. ``"OFF"`` disables all
            logging.

    Returns:
        Active log level name after configuration completes.
    """
    if log_level is None:
        _configure_pymodbus_logger()
        return current_log_level_name()

    resolved_log_level = log_level.upper()
    if resolved_log_level == "INHERIT":
        if not _has_parent_logging_configuration():
            logging.disable(logging.CRITICAL)
            _configure_pymodbus_logger()
            return "OFF"
        _configure_pymodbus_logger()
        return current_log_level_name()
    if resolved_log_level == "OFF":
        logging.disable(logging.CRITICAL)
        _configure_pymodbus_logger()
        return "OFF"

    logging.disable(logging.NOTSET)
    level_value = getattr(logging, resolved_log_level, logging.WARNING)
    logging.basicConfig(level=level_value, format=_LOG_FORMAT, force=True, stream=sys.stdout)
    root_logger = logging.getLogger()
    root_logger.setLevel(level_value)
    for handler in root_logger.handlers:
        handler.setLevel(level_value)
    _configure_pymodbus_logger()
    return resolved_log_level


def set_runtime_log_level(args: Sequence[str]) -> str:
    """Report or update log level for running interactive CLI.

    Args:
        args: Tokens following ``loglevel`` command.

    Returns:
        Human-readable status message.

    Raises:
        ValueError: If invalid argument count or unsupported level supplied.
    """
    if not args:
        return f"Current log level: {current_log_level_name()}"
    if len(args) != 1:
        raise ValueError(f"Usage: loglevel [{'|'.join(LOG_LEVEL_CHOICES)}]")

    requested_level = args[0].upper()
    if requested_level not in LOG_LEVEL_CHOICES:
        raise ValueError(f"Unsupported log level {args[0]!r}. Choose from {', '.join(LOG_LEVEL_CHOICES)}")

    active_level = configure_logging(requested_level)
    return f"Log level set to {active_level}"


__all__ = [
    "LOG_LEVEL_CHOICES",
    "INHERITED_LOG_LEVEL_CHOICES",
    "configure_logging",
    "current_log_level_name",
    "set_runtime_log_level",
]
