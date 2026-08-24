import extended_features as EXT
import multiplayer_features as MP
import update_checker as UPDATE

# Backend hooks must be installed before ui_qt.main() starts worker threads.
EXT.install_backend()
MP.install_backend()

import ui_qt

# Window patches are layered: extended features, multiplayer, then updater.
EXT.install_ui(ui_qt)
MP.install_ui(ui_qt)
UPDATE.install_ui(ui_qt)


if __name__ == "__main__":
    raise SystemExit(ui_qt.main())
