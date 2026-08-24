import extended_features as EXT
import multiplayer_features as MP
import network_features as NET
import update_checker as UPDATE

# Backend hooks must be installed before ui_qt.main() starts worker threads.
EXT.install_backend()
MP.install_backend()
NET.install_backend()

import ui_qt

# Preserve existing extensions, then add network/relay UI, then updater.
EXT.install_ui(ui_qt)
MP.install_ui(ui_qt)
NET.install_ui(ui_qt)
UPDATE.install_ui(ui_qt)


if __name__ == "__main__":
    raise SystemExit(ui_qt.main())
