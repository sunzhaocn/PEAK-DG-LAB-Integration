"""Compatibility alias for :mod:`coyote_app.ui.qt`."""
import sys as _sys
from coyote_app.ui import qt as _impl

_sys.modules[__name__] = _impl
