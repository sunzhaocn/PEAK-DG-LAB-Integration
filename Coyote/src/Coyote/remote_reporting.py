"""Compatibility alias for :mod:`coyote_app.features.reporting`."""
import sys as _sys
from coyote_app.features import reporting as _impl

_sys.modules[__name__] = _impl
