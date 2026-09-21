# Hozz for Android

This is the first Android shell for Hozz. It imports the lossless Hozz NDJSON
archive through Android's Storage Access Framework, keeps a local canonical
store, shows archive-only records honestly, and can explicitly project the
mapped subset into Health Connect on Android 14/API 34 or newer.

It does not read Apple Health on Android and it does not use Health Connect as
the archive. Apple Health extraction still happens on an Apple device.

This project packages a downloadable sideload beta, not a Google Play release
or policy-approval claim. The beta includes experimental, explicitly opted-in
Health Connect writes on supported devices. Signing and publication still
require release-owner approval. See [beta installation, safety and packaging](../docs/android-beta.md).

## Build

```bash
cd Android
./gradlew --no-daemon --no-parallel --max-workers=1 testDebugUnitTest lintRelease assembleRelease
```

Use Android Studio's bundled JDK (`JAVA_HOME`) and the Android SDK
(`ANDROID_HOME`). The existing toolchain compiles against API 37, targets
Android 16/API 36, and supports the canonical archive
workflow on Android 9/API 28 or newer. Health Connect projection requires the
system module on Android 14/API 34 or newer. Hozz deliberately reports
projection as unavailable on Android 9 through 13 even when the standalone
Health Connect provider is installed: its identifier-delete contract rejects a
retry after the first delete succeeds, so a process death before Hozz commits
its ledger cannot be reconciled safely without requesting broad historical
read access.

Release builds are non-debuggable and unsigned by default, with explicit beta
version `0.1.0-beta.1` / code `10001`. `tools/android-beta.py` creates and verifies
a signed APK using owner-controlled external signing inputs; missing inputs
fail closed. `--unsigned` is only for packaging verification, not distribution.
The macOS `tools/android-beta-signing.py package` wrapper rereads the dedicated
release password privately from Keychain. Distribution signing requires a clean
committed tree; an explicit `--candidate` is signed but never distributable.
Archive export selects a local/SD-card folder and publishes a verified new ZIP;
it does not overwrite existing documents or export directly to cloud providers.

`tools/generate-shared-contracts.py` owns the generated mapping and colour
sources. Gradle rejects a build when they drift from `schema/` or
`Sources/HozzUI/HozzPalette.swift`.

## Launcher artwork

Android's adaptive and round launcher icons reuse the existing Hozz master:
`App/Assets.xcassets/AppIcon.appiconset/icon-1024.png`. This is Hozz contributor
artwork under the repository's **GPL-3.0-only** license (`LICENSE`), not a
vendored Tabler icon or a new visual identity.

`python3 tools/android-beta-icon.py` deterministically separates the master's
row-uniform background from its unchanged pixel-art foreground. The foreground
has an 18% adaptive inset to keep the mark inside Android's safe circle. API 33+
also receives a monochrome mask derived from the same light artwork, retaining
the dark eyes/mouth as cutouts. No AI image generation, palette change or
resizing of the source master is involved.

Regeneration uses Pillow; normal builds do **not** need Pillow. Committed PNGs,
adaptive XMLs and `launcher-icon-provenance.json` carry the generated resources
and source/generator/output hashes. Gradle runs
`python3 tools/android-beta-icon.py --check` using only the Python standard
library. Release packaging also verifies that both manifest icon references
exist and the launcher resource resolves in the APK.
