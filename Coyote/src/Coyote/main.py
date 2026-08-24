import extended_features as EXT
import multiplayer_features as MP
import network_features as NET
import remote_reporting as REPORT
import update_checker as UPDATE

# Backend hooks must be installed before ui_qt.main() starts worker threads.
EXT.install_backend()
MP.install_backend()
NET.install_backend()
REPORT.install_backend()

import ui_qt

# Preserve existing extensions. Reporting is installed after the network UI so
# it can add the privacy controls to the final event-log page and surface relay
# ban/policy status without replacing the network implementation.
EXT.install_ui(ui_qt)
MP.install_ui(ui_qt)
NET.install_ui(ui_qt)
REPORT.install_ui(ui_qt)
UPDATE.install_ui(ui_qt)


if __name__ == "__main__":
    raise SystemExit(ui_qt.main())
