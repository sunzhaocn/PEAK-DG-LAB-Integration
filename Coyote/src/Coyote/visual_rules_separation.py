"""Compatibility alias for :mod:`coyote_app.visual_rules.policy`."""
import sys as _sys
from coyote_app.visual_rules import policy as _impl

_sys.modules[__name__] = _impl
