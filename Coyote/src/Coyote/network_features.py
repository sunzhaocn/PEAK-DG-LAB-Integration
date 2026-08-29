"""Compatibility alias for :mod:`coyote_app.features.network`."""
import sys as _sys
from coyote_app.features import network as _impl

_sys.modules[__name__] = _impl
