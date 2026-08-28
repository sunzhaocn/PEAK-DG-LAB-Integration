# Notices and source boundaries

## Project status

PEAK-DG-LAB-Integration (Coyote) is an unofficial community project. It is not affiliated with, endorsed by, or distributed by PEAK, DG-LAB, BepInEx, Thunderstore, or their respective maintainers.

The project is distributed under **GPL-3.0**. The repository-root `LICENSE` and `Coyote/LICENSE` intentionally contain the same license text.

## DG-LAB WebSocket server subtree

`Coyote/dglab-websocket-server-main/` is vendored DG-LAB WebSocket server source derived from the public `dungeonlab-open/dglab-websocket-server` project and retains its GPL-3.0 license.

The vendored files must be treated as a third-party/upstream subtree. They are not claimed to be an exact mirror of the current upstream `main` branch: the project may carry a version selected for Coyote compatibility. When updating that subtree, preserve its upstream license and document the chosen upstream revision in the commit or release notes.

## Terminology

Within Coyote, **“official relay” / `official_relay` means the relay endpoint preconfigured by this Coyote project**. It does **not** mean an official DG-LAB-operated service and does not imply DG-LAB endorsement.

## External build/runtime components

The project also interoperates with PEAK, BepInEx, ThunderPipe/Thunderstore tooling, Bun, Qt/PySide6 and other third-party components. Their trademarks, binaries and licenses remain governed by their respective owners and terms.
