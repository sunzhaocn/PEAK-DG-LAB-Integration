import extended_features as EXT
import multiplayer_features as MP
import network_features as NET
import remote_reporting as REPORT
import update_checker as UPDATE
import visual_rules as VIS
import visual_rules_hardening as VIS_HARDENING
import visual_rules_separation as VIS_SEPARATION

# Backend hooks must be installed before ui_qt.main() starts worker threads.
# Visual rules are installed last so their event bridge sees the final rule set
# registered by the existing extensions.
EXT.install_backend()
MP.install_backend()
NET.install_backend()
REPORT.install_backend()
VIS.install_backend()
VIS_HARDENING.install()
# Final semantic layer: built-in rules and visual custom rules are independent.
# In particular, disable_builtin also disables built-in death/passed-out safety,
# while visual rules must opt into their own death/passed-out protection nodes.
VIS_SEPARATION.install()

import ui_qt

# Preserve existing extensions. Reporting is installed after the network UI so
# it can add the privacy controls to the final event-log page and surface relay
# ban/policy status without replacing the network implementation.
EXT.install_ui(ui_qt)
MP.install_ui(ui_qt)
NET.install_ui(ui_qt)
REPORT.install_ui(ui_qt)
UPDATE.install_ui(ui_qt)
# Install the graph editor last so it replaces only the legacy Python custom-rule
# page after every other UI extension has finished subclassing Window.
VIS.install_ui(ui_qt)
# Then enrich the graph editor with detector-owned parameters (speed threshold,
# item filters, recovery/area settings) and the existing intensity-ramp options.
VIS_HARDENING.install_ui(ui_qt)
# Finally expose custom-only death/passed-out guard modules and the separated
# built-in/custom semantics in the visual editor.
VIS_SEPARATION.install_ui(ui_qt)


if __name__ == "__main__":
    raise SystemExit(ui_qt.main())
