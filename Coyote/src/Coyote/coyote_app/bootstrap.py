"""Application composition root for the Coyote desktop client.

Keep installation order here. Feature modules intentionally wrap selected
backend/UI hooks, so backend extensions must be installed before the Qt module
is imported and UI extensions must be applied in the documented order.
"""
from __future__ import annotations

from collections.abc import Callable

from coyote_app.features import extended as EXT
from coyote_app.features import multiplayer as MP
from coyote_app.features import network as NET
from coyote_app.features import reporting as REPORT
from coyote_app.visual_rules import engine as VIS
from coyote_app.visual_rules import integration as VIS_INTEGRATION
from coyote_app.visual_rules import policy as VIS_POLICY
from coyote_app.visual_rules import usability as VIS_USABILITY
import update_checker as UPDATE


BackendInstaller = Callable[[], object]
UiInstaller = Callable[[object], object]


_BACKEND_INSTALLERS: tuple[BackendInstaller, ...] = (
    EXT.install_backend,
    MP.install_backend,
    NET.install_backend,
    REPORT.install_backend,
    VIS.install_backend,
    VIS_INTEGRATION.install,
    VIS_POLICY.install,
)


def install_backend_extensions() -> None:
    """Install backend hooks once, in dependency order."""
    for installer in _BACKEND_INSTALLERS:
        installer()


def install_ui_extensions(ui_module) -> None:
    """Install UI wrappers after the final Qt module has been imported."""
    installers: tuple[UiInstaller, ...] = (
        EXT.install_ui,
        MP.install_ui,
        NET.install_ui,
        REPORT.install_ui,
        UPDATE.install_ui,
        VIS.install_ui,
        VIS_INTEGRATION.install_ui,
        VIS_POLICY.install_ui,
        VIS_USABILITY.install_ui,
    )
    for installer in installers:
        installer(ui_module)


def run() -> int:
    """Initialize extensions, build the Qt application and enter its event loop."""
    install_backend_extensions()

    # Import after backend hook installation: ui_qt historically starts workers
    # from its startup path and must observe the fully extended backend API.
    from coyote_app.ui import qt as ui_qt

    install_ui_extensions(ui_qt)
    return int(ui_qt.main())
