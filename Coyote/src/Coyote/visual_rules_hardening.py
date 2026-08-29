"""Compatibility alias for :mod:`coyote_app.visual_rules.integration`."""
import sys as _sys
from coyote_app.visual_rules import integration as _impl

_sys.modules[__name__] = _impl
