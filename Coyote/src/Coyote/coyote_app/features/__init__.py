"""Feature extensions for the Coyote desktop runtime.

Modules in this package extend the stable backend through idempotent
``install_backend`` / ``install_ui`` hooks. Importing this package itself has no
installation side effects; the composition root controls ordering explicitly.
"""
