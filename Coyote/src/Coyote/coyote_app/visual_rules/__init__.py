"""Visual-rule subsystem.

- ``engine``: graph model, persistence, evaluation and base editor.
- ``integration``: integration with existing detectors/output modifiers.
- ``policy``: strict separation between built-in and custom rule domains.

Installation is orchestrated by :mod:`coyote_app.bootstrap`; importing this
package alone does not install hooks.
"""
