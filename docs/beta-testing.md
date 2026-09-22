# Testing the Hozz beta

Hozz moves a copy of your health data to destinations you choose. Keep the
original data and a separate backup. The beta is not medical advice and should
not be used to make treatment decisions.

## Get a build

The public beta is available through the links below and the repository's
[GitHub release](https://github.com/brandomoore/Hozz/releases/tag/v0.1.0-beta.1).
Keep the original data and use the release's checksums when downloading.

- **Android:** [download the signed APK](https://github.com/brandomoore/Hozz/releases/download/v0.1.0-beta.1/hozz-0.1.0-beta.1-10001-signed.apk),
  not a Google Play release. Follow
  [Android beta instructions](android-beta.md). Android cannot read Apple Health:
  export on an Apple device and choose that file on Android.
- **iPhone/iPad:** [join the approved TestFlight beta](https://testflight.apple.com/join/y7K4DAdF).
  Install TestFlight, accept the invitation, then install Hozz. Requires iOS/iPadOS 17+.
- **Mac:** [download the signed, notarized DMG](https://github.com/brandomoore/Hozz/releases/download/v0.1.0-beta.1/Hozz-0.1.0-1-mac.dmg).
  Open it, drag **Hozz** onto
  **Applications**, eject the disk image, then open Hozz from Applications.
  Requires macOS 14+. Quit an existing Hozz instance before replacing the app;
  do not delete its stored health archive. The ZIP remains an alternative.
  Do not bypass an operating-system untrusted-software warning.

## Start without personal data

The release can include `hozz-synthetic-demo.zip`. It contains **invented**
data: 240 step-count records, one sleep-stage record, and one tombstone for an
already-deleted synthetic record. On an empty Android archive there should be
241 live timeline records and one retained deletion. All its types are
archive-only in this beta, so it does not offer Health Connect writes.

Use the demo in a fresh test installation, not an archive holding your real
history: imported demo records persist, and the beta has no promise of
one-click demo cleanup. The source name identifies the records as synthetic.

1. Open the ZIP using **Open Hozz archive**.
2. Scroll through the timeline, including past the first 200 records.
3. Import the same file again. The live record count must not increase.
4. Export to a new file in a supported local Android folder. Keep the original.
5. Reimport that exported ZIP. The live count must stay unchanged, and
   archive-only records and the deletion must remain in the archive.

Maintainers can recreate the fixture without any health access:

```bash
python3 tools/create-beta-demo.py build/hozz-synthetic-demo.zip
```

## Test your own workflow

On iPhone, start with a small selection of types and an explicit destination.
Check the per-type completion state; an empty type is successful, while an
incomplete history is not proof of inactivity. Interrupt and resume a transfer,
then verify the destination does not duplicate records.

For Android, use a Hozz NDJSON or ZIP archive, not Apple's raw Health XML export.
Importing must not alter Health Connect by itself. Writing the mapped subset
requires a separate preview, permission, and confirmation; deletion requires
confirmation as well.

**Health Connect writes are experimental and opt-in.** A write is a real
change to your Health Connect store and may be read by other apps you have
authorized. Start with data you understand and inspect the warnings first.
Never test this path with invented medical measurements. API availability is
not a statement of Google Play policy approval.

## Known beta limits

- Health Connect projection requires Android 14/API 34+. Archive import and
  export require Android 9/API 28+.
- Steps, distance, active energy, and individual sleep stages stay in the
  archive; the beta does not claim to write faithful totals or sleep sessions.
- Clinical extraction is unavailable. Existing unsupported records are
  preserved in imported archives rather than relabeled as another type.
- Android export creates a new file in a local External Storage document tree;
  cloud-provider export and replacement of existing files are not supported.
- iOS chooses when background work runs. Automatic sync is not a promise of
  immediate or continuous access.
- The Mac local-network receiver currently uses HTTP with token authentication.
  Use a trusted local network; do not expose it to the internet. If this is
  unsuitable, use a folder destination instead.

## Report a problem safely

Use the [beta feedback form](https://github.com/brandomoore/Hozz/issues/new?template=beta-feedback.yml).
Include app version/build, platform/OS, steps, expected versus actual behavior,
and whether the synthetic demo reproduces it.

**GitHub issues are public.** Do not attach real health archives, readings,
credentials, tokens, destination addresses, device IDs, or unredacted
screenshots/logs. A record count, status label, and error category are usually
enough. Reproduce with synthetic data whenever possible.
