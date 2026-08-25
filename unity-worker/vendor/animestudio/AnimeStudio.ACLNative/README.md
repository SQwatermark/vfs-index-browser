# Endfield ACL native bridge

This library decodes the ACL 2.1 transform and scalar track buffers used by
Arknights: Endfield. It intentionally uses a separate `acl_endfield` library
instead of replacing the older `acl` library used by other games.

The bridge exposes flat frame arrays only. Unity binding lookup and
`AnimationClip` curve creation remain in the managed conversion layer.

## Dependencies

The required headers are vendored to keep release builds reproducible:

- Animation Compression Library:
  `3ee568542eca4428e1041908b4a644b98e7885bd`
- Realtime Math:
  `d046447cfa67d94e1ed15b465fd9cb6fec75e1cb`

Both dependencies use the MIT license. Their license files are included under
`ThirdParty`.

## Build

Run from PowerShell on Windows with the Visual Studio C++ toolchain installed:

```powershell
.\AnimeStudio.ACLNative\build.ps1
```

The script builds both architectures into:

- `AnimeStudio.Libraries\x86\acl_endfield.dll`
- `AnimeStudio.Libraries\x64\acl_endfield.dll`

## Compact animation export

The CLI exposes `--export_type AnimationJSON` for decoded animation data:

```powershell
AnimeStudio.CLI.exe source.ab output `
  --game ArknightsEndfield `
  --types AnimationClip `
  --export_type AnimationJSON
```

The resulting `*.animation.json` uses the
`AnimeStudioAnimationClip` `1.0.0` contract. Curves reference a shared
`timelines` array and keep both the resolved `path` and original Unity
`pathHash`. Consumers should use `pathHash` as identity because standalone
animation bundles do not always contain a path string table.

Euler and object-reference curves are not yet represented by this contract.
The exporter fails explicitly when either is present instead of producing a
partial animation silently.
