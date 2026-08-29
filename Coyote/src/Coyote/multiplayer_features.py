"""Compatibility alias for :mod:`coyote_app.features.multiplayer`."""
import sys as _sys
from coyote_app.features import multiplayer as _impl

_sys.modules[__name__] = _impl
