# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Explicit linear EDCON axis implementation."""

from .edcon_axis import EdconAxis


class LinearAxis(EdconAxis):
    """EDCON axis whose public position and velocity units are mm and mm/s.

    This named subclass makes linear Modbus axes explicit while preserving the
    behavior of direct ``EdconAxis`` construction.
    """

    _coordinate_kind = "linear"
