"""Unit tests for EdconAxis construction and identity.

Coverage areas
--------------
* ``EdconAxis.__init__`` — attribute storage, ComModbus instantiation,
  SW-limit PNU reads, max_speed calculation, and the MotionHandler method
  calls that occur during initialisation.
* ``EdconAxis.__eq__`` — equality semantics and the NotImplementedError
  guard against cross-type comparisons.

No hardware or network connection required.  All tests either use the
``axis_mock`` fixture (which patches ``ComModbus`` and ``MotionHandler``)
or construct bare instances via ``object.__new__`` for equality tests.
"""

from unittest.mock import patch, MagicMock

import pytest

from festo_dev_applied_motion.backends.edcon_axis import EdconAxis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_axis(name: str = "TEST", ip: str = "192.168.0.1") -> EdconAxis:
    """Return a EdconAxis whose __init__ has been bypassed entirely.

    Only ``name`` and ``ip`` are set; suitable for equality tests that
    do not exercise any MotionHandler behaviour.
    """
    axis = object.__new__(EdconAxis)
    axis.name = name
    axis.ip = ip
    return axis


# ---------------------------------------------------------------------------
# EdconAxis.__init__
# ---------------------------------------------------------------------------


class TestEdconAxisInitialization:
    """Verify that EdconAxis.__init__ stores the right state and delegates
    correctly to its hardware dependencies."""

    def test_name_attribute_stored(self, axis_mock):
        assert axis_mock.name == "X"

    def test_ip_attribute_stored(self, axis_mock):
        assert axis_mock.ip == "192.168.0.193"

    def test_commodbus_instantiated_with_ip(self, axis_mock, mocker):
        """ComModbus must be called exactly once with the axis IP so the
        constructor connects to the correct drive on the network."""
        # axis_mock was built with the default IP; inspect the patch call
        # by constructing a second axis with a known IP via the same patch.
        mock_com = MagicMock()
        mock_com.read_pnu.return_value = 0

        from edcon.edrive.motion_handler import MotionHandler

        with patch("festo_dev_applied_motion.backends.edcon_axis.ComModbus", return_value=mock_com) as mock_cls:
            with patch.object(MotionHandler, "__init__", lambda self, com: setattr(self, "min_velocity", 0.0) or setattr(self, "max_velocity", 0.0)):
                with patch.object(MotionHandler, "acknowledge_faults"):
                    with patch.object(MotionHandler, "configure_software_limit_switch"):
                        with patch.object(MotionHandler, "fault_present", return_value=False):
                            with patch.object(MotionHandler, "fault_string", return_value=""):
                                with patch.object(MotionHandler, "current_fault_code", return_value=0):
                                    axis = EdconAxis(name="Y", ip="10.0.0.99")
            mock_cls.assert_called_once_with("10.0.0.99")

    def test_neg_sw_limit_read_from_pnu_11584(self, axis_mock):
        """Negative SW limit must be loaded from PNU 11584 so the axis
        correctly bounds all subsequent relative and absolute moves."""
        assert axis_mock._neg_sw_limit == -300_000

    def test_pos_sw_limit_read_from_pnu_11585(self, axis_mock):
        """Positive SW limit must be loaded from PNU 11585."""
        assert axis_mock._pos_sw_limit == 300_000

    def test_max_speed_equals_max_of_absolute_velocity_bounds(self, axis_mock):
        """max_speed must be the larger of |min_velocity| and |max_velocity|
        so callers can always clamp to the physically achievable top speed."""
        expected = max(abs(-500.0), abs(500.0))
        assert axis_mock.max_speed == expected

    def test_acknowledge_faults_called_during_init(self, axis_mock):
        """acknowledge_faults must be called during __init__ to clear any
        latched errors before the axis is used; skipping it risks leaving
        the drive in a locked state."""
        from edcon.edrive.motion_handler import MotionHandler

        # The mock was set up on MotionHandler; access it via the class to
        # check it was called on the instance.
        MotionHandler.acknowledge_faults.assert_called()

    def test_configure_software_limit_switch_enabled_during_init(self, axis_mock):
        """Software limit switch must be activated during __init__ so that
        moves are bounded by the loaded SW limits from the first command."""
        from edcon.edrive.motion_handler import MotionHandler

        MotionHandler.configure_software_limit_switch.assert_called_with(True)

    def test_com_attribute_is_the_mocked_com_object(self, axis_mock):
        """axis.com must be the exact object returned by ComModbus() so
        that downstream methods share the same mock and call records."""
        assert axis_mock.com is axis_mock._mock_com


# ---------------------------------------------------------------------------
# EdconAxis.__eq__
# ---------------------------------------------------------------------------


class TestEdconAxisEquality:
    """Verify the equality contract for EdconAxis instances."""

    def test_identical_name_and_ip_are_equal(self):
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("X", "192.168.0.193")
        assert a == b

    def test_different_names_are_not_equal(self):
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("Z", "192.168.0.193")
        assert a != b

    def test_different_ips_are_not_equal(self):
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("X", "192.168.0.32")
        assert a != b

    def test_different_name_and_ip_are_not_equal(self):
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("Z", "192.168.0.32")
        assert a != b

    def test_comparison_with_non_axis_raises_not_implemented(self):
        """Comparing a EdconAxis with any non-EdconAxis object must raise
        NotImplementedError so callers receive a clear diagnostic instead of
        a silent False."""
        axis = _bare_axis()
        with pytest.raises(NotImplementedError):
            axis == "not-an-axis"

    def test_comparison_with_none_raises_not_implemented(self):
        axis = _bare_axis()
        with pytest.raises(NotImplementedError):
            axis == None  # noqa: E711 — intentional direct comparison

    def test_comparison_with_dict_raises_not_implemented(self):
        axis = _bare_axis()
        with pytest.raises(NotImplementedError):
            axis == {"name": "X", "ip": "192.168.0.193"}


# ---------------------------------------------------------------------------
# EdconAxis.__repr__
# ---------------------------------------------------------------------------


class TestEdconAxisRepr:
    """Verify the __repr__ contract for EdconAxis instances."""

    def test_repr_contains_class_name(self):
        axis = _bare_axis("X", "192.168.0.1")
        assert "EdconAxis" in repr(axis)

    def test_repr_contains_name(self):
        axis = _bare_axis("X", "192.168.0.1")
        assert "X" in repr(axis)

    def test_repr_contains_ip(self):
        axis = _bare_axis("X", "192.168.0.193")
        assert "192.168.0.193" in repr(axis)

    def test_repr_format(self):
        axis = _bare_axis("Y", "10.0.0.5")
        assert repr(axis) == "EdconAxis(name='Y', ip='10.0.0.5')"


# ---------------------------------------------------------------------------
# EdconAxis.__hash__
# ---------------------------------------------------------------------------


class TestEdconAxisHash:
    """Verify the __hash__ contract for EdconAxis instances."""

    def test_equal_axes_have_equal_hashes(self):
        """Axes with the same name and ip must produce the same hash so they
        can be used interchangeably in sets and dicts."""
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("X", "192.168.0.193")
        assert hash(a) == hash(b)

    def test_axis_usable_as_dict_key(self):
        axis = _bare_axis("X", "192.168.0.193")
        d = {axis: "value"}
        assert d[axis] == "value"

    def test_axis_usable_in_set(self):
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("X", "192.168.0.193")
        s = {a, b}
        assert len(s) == 1

    def test_different_axes_likely_different_hashes(self):
        """Different name/ip combinations should produce different hashes.
        Hash collisions are theoretically possible but extremely unlikely
        for typical axis configurations."""
        a = _bare_axis("X", "192.168.0.193")
        b = _bare_axis("Z", "192.168.0.32")
        assert hash(a) != hash(b)
