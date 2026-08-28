# Versioning

Coyote contains multiple independently versioned artifacts. Their numbers are not required to be identical.

## Desktop application / GitHub Release

Source: `Coyote/src/Coyote/app_version.py` (`APP_VERSION`).

This is the version shown by the desktop application's update system and should match the corresponding GitHub Release tag, normally with an optional leading `v`.

## BepInEx plugin assembly

Source: `Coyote/src/Coyote/Coyote.csproj` (`<Version>`).

This versions the compiled `Coyote.dll` / BepInEx plugin package. Increment it when the plugin assembly changes in a way that should be released, independently of desktop-only changes.

## Feature-module revision labels

Strings such as `V2.6.x` in network/reporting module docstrings describe the revision of that subsystem. They are implementation/revision labels and are not the public desktop release version.

## Relay server

`PEAK_Coyote_Relay` is a separate repository and has its own version lifecycle. A relay version must not be inferred from the desktop application version.

When publishing a release, state explicitly which desktop version, plugin version and relay compatibility were tested.
