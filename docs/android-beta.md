# Android downloadable beta

This preview is a **signed APK for sideloading**, not a Google Play release or a
claim of Google Play/Health Connect policy approval. Experimental Health Connect
writes are included by explicit product choice, **off until you choose them**.
Keep your original archive. Hozz transfers records; it is not medical advice,
diagnosis, a clinical record system, or a replacement for your source health app.

## Install and update safely

1. Obtain the signed `hozz-…-signed.apk`, `SHA256SUMS`, and `beta.json` from the
   maintainer's announced release location. No download is implied by this
   document. Never install an APK marked `UNSIGNED-NOT-FOR-DISTRIBUTION`.
2. Compare the APK SHA-256 with the maintainer's checksum and the signing
   certificate SHA-256 with a previously trusted release (see verification
   below). A checksum alone does not establish who published a file.
3. On Android 9/API 28 or newer, open the APK and grant **Install unknown apps**
   only to the file manager/browser you use. Turn that permission off afterward.
   Do not disable Play Protect or other system protections to bypass a warning.
4. Open Hozz and choose **Open Hozz archive**. Importing does not request Health
   Connect access or write to Health Connect. Reimport updates the same canonical
   records rather than duplicating them.
5. For updates, install a higher-version APK signed with the **same release
   certificate** over the existing app. The app ID stays `com.thatcube.hozz`.
   Do not uninstall or clear storage as an update step.

An old developer/debug build normally has a different certificate: Android
correctly refuses an in-place release update. **Do not uninstall to get past
this error.** First export and verify your archive, retain your source files,
and contact the maintainer about migration. The exported archive does not
contain the local Health Connect projection ledger; uninstalling or clearing
storage loses that ledger and may leave projected records in Health Connect.
There is no supported automatic recovery of untracked prototype writes.

## What each platform does

- Apple Health extraction happens on iPhone. A user-controlled destination,
  such as the Mac receiver, can accumulate the archive. Android cannot read
  Apple Health, pair through Apple's Keychain, or directly receive the iPhone
  export service.
- Android opens Hozz NDJSON or ZIP files through the system picker, stores
  canonical records locally, displays a paged timeline, and exports a lossless
  versioned Hozz ZIP. Provenance, unknown fields, run records and tombstones are
  retained. Health Connect is a partial destination, **not the archive**.
- **Save Hozz archive** asks for an on-device/SD-card folder. Hozz stages a ZIP
  privately, writes and verifies a unique temporary document, then renames and
  verifies it. It does not replace an existing archive. Cloud/proxy document
  providers are rejected for export; copy a completed ZIP yourself if desired.

## Experimental Health Connect

Android 14/API 34 or newer with the system Health Connect module is required.
Android 9–13 remain archive-only even with the older standalone provider.
Review the insert/update/delete and exact/lossy/archive-only counts, tap
**Write mapped records to Health Connect**, read the confirmation, and choose
whether to continue. Nothing is written on launch, import, or cancellation.
Only the needed write permissions are requested; no broad read, history or
background-read access is requested. Other apps you authorize in Health Connect
may read records you put there.

On a device using Health Connect for the first time, its **Get started**
onboarding can appear before the system permission prompt. That prompt lists
only the write types needed by your preview. Choosing **Don't allow** leaves
the archive intact and reports that no records were written. No shell-granted
test permission or Google Play account is required to reach this prompt on the
tested Android 16/API 36 system module; other device/module versions still need
their own acceptance checks.

Individual heart-rate readings, weight and height have mappings. Exercise
sessions can lose rich details or use a generalized activity; review warnings.
Steps, distance, active energy and sleep stay archive-only pending safe
overlap/session handling. ECGs, clinical records, medication doses, audiograms,
State of Mind, routes, series and other unsupported types remain archive-only.
Clinical extraction on Apple is not complete coverage. See [mapping details](android.md).

Deletion requires a confirmation explicitly naming writes **and deletions**.
Only Hozz-tracked projections are removed; canonical tombstones remain exportable.
Legacy write declarations for steps/distance/energy/sleep allow ledger-backed
cleanup only, not new projections. The durable journal and deterministic client
IDs support retry/restart recovery; they cannot repair an erased ledger.

## Storage and privacy

The Android archive resides in the app's private SQLite database. Android backup
is disabled. Uninstalling/clearing storage loses this database and its ledger;
export before changing installations. There is no account, analytics, automatic
upload, or Internet permission in this beta. Exported ZIP/NDJSON files are
**not encrypted**: choose a trusted folder and protect transferred copies.

Import is all-or-nothing and validates bounded archives: at most 1,024 ZIP
entries, 64 GiB inflated input, 50 million lines, 448 KiB per legacy line or
512 KiB per strict canonical record, with additional compression-ratio limits.
These are rejection ceilings, **not tested capacity or free-space guarantees**.
Allow storage for input, the database, import staging, and a complete staged
export plus its destination copy. Timeline pages are byte bounded; only a
bounded window remains in the UI. There is no automatic archive pruning,
background synchronization, or automatic Health Connect projection.

Report app version/code, Android version, operation, and a redacted error
message. Do not post health archives, sample values, screenshots containing
personal records, account information, credentials, or unreviewed system logs.
Prefer reproductions using the synthetic fixture below.

## Safe synthetic demonstration and acceptance

Generate the archive-only demo (use a new output path):

```bash
python3 tools/create-beta-demo.py Android/build/demo/hozz-synthetic-demo.zip
```

It contains **242 canonical records: 240 synthetic step counts, one sleep
stage, and one distinct tombstone**. All are archive-only: the Health Connect
write action remains disabled. On a fresh isolated test installation, import
twice: expect 242 new records, then 242 already current, not duplicates.
The timeline initially shows 200 records and loads the remaining live records
as you scroll; eventually it shows 241 live records, excluding the tombstone.
Save to a local folder and reimport the exported ZIP: all 242 canonical records,
including the tombstone, remain current.

The smaller `schema/hozz/v1/fixtures/canonical-records.ndjson` fixture contains
weight, sleep and ECG records; unlike the demo, one record is eligible for
Health Connect. Use it only on an **isolated test emulator/device**.
**Do not write synthetic samples into your personal Health Connect store.**

Instrumented `BetaTransferAcceptanceTest` checks synthetic import/reimport,
newer tombstone and stale replay, deterministic ZIP export, strict reimport,
provenance and unknown-field preservation, and an untouched projection ledger.
`HozzTimelinePaginationTest` covers record/byte-bounded paging; `BetaConsentTest`
checks cancel/no-write and explicit write/delete approval. Existing
`HealthConnectWriterIntegrationTest` uses synthetic platform records and must
only run on an isolated emulator; it must not target a personal phone.

## Maintainer packaging (no publication)

Use the existing Gradle wrapper and dependencies. Compile SDK is 37, target SDK
36, minimum SDK 28; installed build tools 36.0.0 are used for artifact checks.
Set `JAVA_HOME` to Android Studio's bundled JDK and `ANDROID_HOME` to your SDK.
The verified local JDK is 25.0.2; for Linux CI, use JDK 25 with the checked-in
Gradle 9.7.1 wrapper, AGP 9.3.2 and Kotlin 2.4.10. The SDK Manager identifiers
are `platforms;android-37.0`, `build-tools;36.0.0` and `platform-tools`.
The script uses one Gradle worker, no parallel tasks, a 2 GiB heap, and records
raw Gradle output. It does not install SDKs, create keys, upload, or publish.

```bash
python3 tools/android-beta-test.py
cd Android
./gradlew --no-daemon --no-parallel --max-workers=1 \
  -Dorg.gradle.jvmargs="-Xmx2g -Dfile.encoding=UTF-8" \
  -Pkotlin.compiler.execution.strategy=in-process \
  testDebugUnitTest lintRelease assembleRelease
cd ..
python3 tools/android-beta.py --version-name 0.1.0-beta.1 \
  --version-code 10001 --output Android/build/beta/0.1.0-beta.1
```

### macOS Keychain-backed release identity

An owner-authorized dedicated release identity is kept outside the checkout:
`~/.config/hozz/signing/hozz-android-release.p12` (file mode `0600`, containing
directory `0700`). The password is stored in the user's macOS Keychain under
service `com.thatcube.hozz.android-release`, account `release-keystore`.
`release.json` in that private directory contains only identity/verification
metadata, not the password. The public certificate SHA-256 is:

```text
d3d4899638947f6d688b008a3ccdc8f567df9633cd6b3b2105cad4cbad67df1c
```

**Before publication, the owner must back up the encrypted keystore and its
password separately in recoverable, owner-controlled storage.** Keychain
storage alone is not a verified backup. Losing the key or its password prevents
updates to existing installations; do not generate a replacement casually.
Never put either into a repository, issue, build log, or chat.

Verify/reuse the existing identity without displaying its password:

```bash
python3 tools/android-beta-signing.py status
python3 tools/android-beta-signing.py package --version-name 0.1.0-beta.1 \
  --version-code 10001 --output Android/build/beta/0.1.0-beta.1-signed
```

This wrapper rereads the exact Keychain entry, checks file ownership/modes and
the pinned certificate, then supplies signing inputs privately to the packaging
process. It does not change Gradle's unsigned release behavior or affect CI.
New identity creation is a separate, explicitly authorized operation:
`python3 tools/android-beta-signing.py setup --owner-authorized`.
**Do not run setup to reuse or repair an existing key.** Existing accounts,
keys, nonempty storage or interrupted setup cause a failure, not an overwrite
or silent identity rotation.

Before a **signed** invocation, the release owner must provision/authorize and
back up a stable external keystore. No release key is supplied by
this repository. For signing outside the macOS wrapper, provide these environment variables through an owner-controlled
secret manager or a hidden interactive prompt, never literal shell-history
passwords, command-line password arguments, or committed configuration:

| Variable | Value |
| --- | --- |
| `HOZZ_ANDROID_KEYSTORE` | Absolute keystore path outside the checkout |
| `HOZZ_ANDROID_KEY_ALIAS` | Owner-chosen release-key alias |
| `HOZZ_ANDROID_STORE_PASSWORD` | Keystore password |
| `HOZZ_ANDROID_KEY_PASSWORD` | Key password |
| `HOZZ_ANDROID_CERT_SHA256` | Trusted certificate SHA-256 (64 hex digits, colons optional) |

Never rotate/substitute this certificate casually: it controls future updates.
The release owner chooses the identity; this script never generates a key,
uses a debug-key fallback, or grants distribution-policy approval. Signing
passwords are passed to `apksigner` by environment reference and are removed
from the Gradle environment. Signing error output is not logged.

If signing is not authorized, append `--unsigned` and choose a new output
directory. That verifies packaging only and produces a prominently marked
**non-installable, non-distributable** unsigned artifact.

Each invocation requires a new output directory and explicit beta version/code.
Increase `versionCode` for every published update. Gradle's matching properties
are `hozzVersionName` and `hozzVersionCode`; defaults are `0.1.0-beta.1`/`10001`.
Signing is deliberately a separate `apksigner` step, keeping release secrets out
of Gradle configuration/cache. A plain Gradle release remains unsigned.

Distribution signing also requires a **clean committed tree**. The source
commit must remain unchanged and the tree clean through verification; finish
and commit all release changes before generating the final artifact. An
explicit `--candidate` (supported by both packaging entrypoints) permits a
signed dirty-tree test but labels the filename
`signed-CANDIDATE-NOT-FOR-DISTRIBUTION` and sets `distributable: false`.
It is never a publishable substitute for rebuilding from the final commit.

The pipeline checks alignment, signature and pinned certificate, application ID,
versions, SDK bounds, resolved launcher/round icon resources,
absence of debuggability/test activity/network permission,
and the exact Health Connect write-only declaration set **before** moving the
APK out of its verification-work folder. It emits `beta.json`, `SHA256SUMS`,
`signature.txt`, manifest/badging evidence and `gradle.log`; failed
`verification-work` contents are not release artifacts. `beta.json` records the
source commit and whether the checkout was dirty. For reproducible release
inputs, use the same clean commit, toolchain, version and signing key.

Independent verification:

```bash
cd Android/build/beta/0.1.0-beta.1
shasum -a 256 -c SHA256SUMS
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs \
  hozz-0.1.0-beta.1-10001-signed.apk
```

Do not publish an unsigned artifact, an unverified work-folder APK, or a
debug-signed APK. Distribution and its chosen host require separate owner
approval; this pipeline performs no cloud release.
