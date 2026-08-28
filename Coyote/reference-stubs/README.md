# PEAK compile-time reference stubs

This directory contains declaration-only C# stubs used by GitHub Actions to compile `Coyote.dll` without redistributing PEAK game assemblies.

These files contain only the minimum type/member signatures referenced by the Coyote plugin. They are **not** game code and are never shipped in the portable release. At runtime, Coyote binds to PEAK's real `Assembly-CSharp.dll` from the user's installed game.

Local developer builds should prefer the real PEAK `Managed` directory. CI sets the `PEAK_REFERENCE_ASSEMBLY` MSBuild property to the stub assembly explicitly.

When PEAK changes an API used by Coyote, update the stubs only after verifying the corresponding signature against a legitimate local PEAK installation and keep the declarations no broader than necessary.
