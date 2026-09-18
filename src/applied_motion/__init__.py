# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

__copyright__ = "Copyright (c) 2026 Festo SE & Co. KG"

__license__ = "MIT"

__full_license__ = """All right reserved

MIT License

Copyright (c) 2026 Festo SE & Co. KG

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

__all__ = [
    "Gantry",
    "EdconAxis",
    "MovementError",
    "AxisNotFoundError",
    "Axis",
    "FPosBAPIClient",
    "FPosBAPIClientError",
    "FPosBAxis",
]

from applied_motion.applied_motion import Gantry, MovementError, AxisNotFoundError
from applied_motion.backends import Axis, FPosBAPIClient, FPosBAPIClientError, FPosBAxis, EdconAxis
