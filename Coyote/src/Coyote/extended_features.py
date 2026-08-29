"""Compatibility alias for :mod:`coyote_app.features.extended`.

New code should import the canonical package module directly.
"""
import sys as _sys
from coyote_app.features import extended as _impl

_sys.modules[__name__] = _impl
