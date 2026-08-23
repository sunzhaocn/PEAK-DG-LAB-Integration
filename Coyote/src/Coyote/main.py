import extended_features as EXT
import multiplayer_features as MP

# Backend hooks must be installed before ui_qt.main() starts the PEAK/DG-LAB
# worker threads.
EXT.install_backend()
MP.install_backend()

import ui_qt

# Window patches are layered: extended rule editor first, multiplayer page last.
EXT.install_ui(ui_qt)
MP.install_ui(ui_qt)


if __name__ == "__main__":
    raise SystemExit(ui_qt.main())
