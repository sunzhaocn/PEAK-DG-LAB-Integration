"""Compatibility alias for :mod:`coyote_app.visual_rules.engine`."""
import sys as _sys
from coyote_app.visual_rules import engine as _impl

_sys.modules[__name__] = _impl
