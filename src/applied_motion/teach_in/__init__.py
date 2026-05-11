# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Interactive teach-in tooling for gantry position recording.

:class:`TeachSession` is available without any extras — it has no
dependency on ``prompt_toolkit`` or ``rich``.

The interactive REPL (:mod:`applied_motion.teach_in.cli`) and the
``applied-motion-teach`` entry point require the ``teach`` extra::

    pip install festo-dev-applied-motion[teach]

Typical usage::

    from applied_motion.teach_in import TeachSession

    session = TeachSession(gantry, on_capture=my_hook)
    session.jog("X", "+", 5.0)
    session.capture("deck_a1")
    session.save("deck_layout.json")

To launch the interactive REPL::

    applied-motion-teach --config gantry.json
"""

from applied_motion.teach_in.session import TeachSession
from applied_motion.teach_in.cli import run_repl

__all__ = ["TeachSession", "run_repl"]
