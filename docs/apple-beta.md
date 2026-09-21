# Apple beta packaging

`tools/apple-beta.sh` is the non-installing release entrypoint for **Hozz**
(iPhone and iPad, iOS 17+) and **HozzMac** (macOS 14+). It never launches the Mac
receiver or installs on a phone. It acquires the existing machine-wide shared
Apple build lease before generation and keeps it through archive, export,
validation, and, when explicitly requested, upload **and processing**, or
notarization **through acceptance, stapling, and verification**. See
[the interlock protocol](disk-reclaim.md). Do not run its Python worker directly.

## Choose a distribution route

- **iPhone/iPad:** TestFlight (`--distribution testflight`, the default).
- **Mac beta:** notarized Developer ID direct download
  (`--distribution developer-id`). This preserves the existing standalone MCP
  tool's access model; it does not require a Mac App Store app record or an
  installer certificate.
- **Mac TestFlight:** supported by the packaging interface, but **not ready**
  with the current unsandboxed embedded MCP executable. Do not change its
  sandbox flags just to pass signing; that would change archive access.

A local package is not a published download. This tool does not create GitHub
releases, publish download links, or invite testers.
[Tester availability and setup](beta-testing.md) describe what is actually
available; a command in this document does not imply an available beta.

## Compile without pretending to distribute

Requirements: Xcode 27+, XcodeGen 2.46+, Python 3.9+, and a gitignored
`Local.xcconfig`. That file may be empty for unsigned builds; the lane never
creates or changes it. Select an installed Xcode with `DEVELOPER_DIR` if needed.
App Store Connect may restrict beta Xcode/SDK submissions independently of what
compiles locally; check its current requirements before uploading.

```bash
tools/apple-beta.sh build ios --version 0.1.0 --build 1
tools/apple-beta.sh build mac --version 0.1.0 --build 1
python3 -m unittest discover -s tools/tests -p 'test_apple_beta.py'
```

Every invocation gets a new, worktree-local
`build/apple-beta/<platform>-<distribution>-<version>-<build>-<action>-<unique-id>/` directory.
Jobs default to two; `--jobs 1` through `--jobs 8` are supported. Outputs are
gitignored, never cleaned or reused by the lane:

- its own `DerivedData`, raw logs, and build `.xcresult`;
- the app, frameworks, widget where applicable, and matching dSYMs;
- `evidence.json` with source commit/dirty status, toolchain log, exact build
  command, requested version/build, validation state, and artifact paths;
- signed lanes additionally retain `.xcarchive`, export options, inspected
  exported contents, and an IPA/PKG/ZIP SHA-256.

The `build` action explicitly disables signing. It validates bundle IDs,
iPhone/iPad support, app/widget version parity, privacy manifests, source
entitlements, and app/dSYM UUID parity. **An unsigned compile is not an
installable beta, a signed archive, or evidence of store acceptance.**

Signed `archive`/export requires a clean committed worktree by default and
freezes the starting HEAD even when `--source-commit` is omitted. For an
explicitly local signing/provisioning probe only:

```bash
tools/apple-beta.sh archive ios --version 0.1.0 --build 1 --candidate
```

`--candidate` labels the result `signed-candidate-non-distributable` with
`eligible_for_apple_submission: false`. It cannot be combined with upload,
notarization, or an approved source commit. It is not a release shortcut.

## Signing prerequisites

Use the same enrolled Apple Developer Program team for iOS and Mac so the
shared Keychain access group can work. Set the team explicitly; this lane never
chooses an account. **Manual, existing-assets-only signing is the default**:

```bash
export HOZZ_TEAM=YOURTEAMID
export HOZZ_IOS_PROFILE='Your installed iOS App Store profile'
export HOZZ_WIDGET_PROFILE='Your installed widget App Store profile'
export HOZZ_MAC_PROFILE='Your installed Mac App Store profile'
tools/apple-beta.sh check ios --version 0.1.0 --build 1
tools/apple-beta.sh check mac --version 0.1.0 --build 1
```

The names/UUIDs refer to **already-installed** distribution profiles:

| Product | Registered bundle ID | Capabilities/profile requirements |
| --- | --- | --- |
| iOS app | `com.thatcube.Hozz` | HealthKit, HealthKit background delivery, App Group `group.com.thatcube.Hozz`, shared Keychain group `<AppIdentifierPrefix>com.thatcube.Hozz.shared` |
| Widget | `com.thatcube.Hozz.widget` | Same App Group; an extension profile, not an App Store app record |
| Mac app | `com.thatcube.Hozz.mac` | App Sandbox, inbound/outbound networking, user-selected read/write files, same shared Keychain group |

The Keychain prefix must agree on both platforms; older accounts can have an App
Identifier Prefix different from their Team ID. Register/configure capabilities
in the developer account deliberately, rather than changing bundle IDs to get
past an error. Clinical Health Records stay disabled in this lane.

Apple's upload validation requires both HealthKit read and update purpose
strings even though Hozz requests no Apple Health write access. The update
string states that read-only behavior truthfully; the packaging validator
rejects either missing string before submission.

TestFlight on either platform requires a usable **Apple Distribution certificate and its
private key** in the local keychain. Mac TestFlight also requires **Mac Installer
Distribution** (shown by Keychain as `3rd Party Mac Developer Installer`).
Apple Development is not distribution signing. Developer ID Application is for
the direct-Mac distribution flow below and is not a substitute for Mac App
Store signing.

`check` verifies local identity/input presence, not profile entitlement contents
or App Store Connect state. Archive/export performs the actual signing checks.
By default, manual signing, target-specific profile settings, and omission of
`-allowProvisioningUpdates` prevent this lane from generating signing assets or
registering devices behind your back. It never embeds or commits a private key.

### Explicitly opt in to automatic provisioning

Only after the account owner grants permission, add
`--allow-provisioning-updates` and supply the named team and an authorized
**account-level** App Store Connect API key:

```bash
export HOZZ_TEAM=YOURTEAMID
export HOZZ_ASC_KEY_PATH=/absolute/private/path/existing-key.p8
export HOZZ_ASC_KEY_ID=YOURKEYID
export HOZZ_ASC_ISSUER_ID=YOUR-ISSUER-UUID
tools/apple-beta.sh archive ios --version 0.1.0 --build 1 --allow-provisioning-updates
```

This explicitly authorizes Xcode's automatic provisioning for that team during
archive **and export**, including creation/download of profiles and signing
assets as permitted by the account. It does not register physical devices,
create App Store app records, or upload a build. Profile-name overrides are
cleared for the automatic lane. Xcode may first archive with Apple Development;
that intermediate archive is labeled **not distributable**. The exported
package must still pass the requested distribution-identity, entitlement, and
debugger-entitlement checks. Account roles/capabilities can still block Xcode.

The flag is forbidden for unsigned `build`. `check` validates the supplied
inputs locally without contacting the account, even when the flag is supplied.
Nothing creates or stores the API key; no account credential is inferred from
another project. A `notarize` or `upload` action still requires its separate
confirmation flag.

### Read-only account readiness before requesting changes

With explicit permission to use the configured account-level key above:

```bash
tools/apple-account.sh inspect --team "$HOZZ_TEAM"
python3 -m unittest discover -s tools/tests -p 'test_apple_*.py'
```

This verifies the team against `Local.xcconfig`, queries only the exact Hozz
app/bundle IDs, their profiles/capabilities/recent builds, and account signing
certificate metadata. It matches public certificate fingerprints to available
local signing identities. Failed queries remain **unknown**, never “missing.”

The report is written with mode 0600 under a unique worktree-local
`build/apple-account/` directory. Private key bytes, JWTs, passwords, certificate
content, provisioning profile bodies, and raw server error bodies are never
logged or saved. JWT signing uses the existing key by path; HTTP requests are
GET-only with redirects refused. The tool never creates a signing key, profile,
certificate, capability, or app record.

Its `proposed_changes` section is a **confirmation checklist**, not an executed
plan. It can prepare an exact profile-creation body when one compatible local
identity is identified; otherwise it identifies the prerequisite and the
separately authorized Xcode automatic-provisioning alternative. It does not
delete invalid profiles or revoke certificates.

An existing iOS app record should be reused. If none exists,
[Apple documents that new app records must be created on the App Store Connect website](https://developer.apple.com/documentation/appstoreconnectapi/apps),
not with the public API. Developer ID Mac distribution requires no Mac App Store
record. Account inspection and local preparation do not authorize a later
TestFlight upload or notarization submission; confirm that exact artifact action
separately.

## Produce a signed local package

```bash
tools/apple-beta.sh archive ios --version 0.1.0 --build 1
tools/apple-beta.sh archive mac --version 0.1.0 --build 1
```

These archive Release and export using `app-store-connect`, without uploading.
The lane verifies signatures and signing team, required entitlements,
debugger-entitlement absence, bundle/version metadata, privacy resources, and
matching dSYMs both before and after export. Mac installer signatures are
checked too. Xcode is not allowed to silently renumber the build.

**Mac App Store gate:** the embedded `hozz-mcp` executable currently has no
sandbox entitlement. The signed lane deliberately rejects that shape. Its
archive-reading behavior when launched by an external assistant must be
reconciled with sandbox/container access before Mac TestFlight can ship.
Blindly adding `com.apple.security.inherit` would not solve an independently
launched command-line client's access. Use the Developer ID path instead.

## Direct Mac: Developer ID, notarization, and a stapled ZIP

The **DMG is the recommended tester download**: it shows Hozz alongside an
Applications shortcut in a large-icon Finder window. Drag the app to
Applications, eject the disk image, then launch the installed app. The signed
app ZIP remains an alternative and the input to the DMG packaging step below.

Required: the existing **Developer ID Application certificate/private key**
for `HOZZ_TEAM`, plus a Developer ID provisioning profile matching
`com.thatcube.Hozz.mac` and its existing shared-Keychain entitlements. A local
archive attempt without that profile fails with Xcode's “HozzMac requires a
provisioning profile”; a valid certificate alone is insufficient. Use an
already-installed profile or, only after authorization, automatic provisioning.
The existing main-app sandbox and the MCP tool's independent access are
unchanged. Both executables must have Hardened Runtime and secure timestamps.
The MCP executable is embedded with Xcode's native Copy Files phase. A shell
copy through `BUILT_PRODUCTS_DIR` follows different symlinks during archive
and can fail under the script sandbox even when ordinary builds pass; the
native phase handles those archive paths without disabling sandboxing.

```bash
export HOZZ_TEAM=YOURTEAMID
export HOZZ_MAC_PROFILE='Your installed Hozz Developer ID profile'
tools/apple-beta.sh check mac --distribution developer-id --version 0.1.0 --build 1
tools/apple-beta.sh archive mac --distribution developer-id --version 0.1.0 --build 1
```

`archive` exports a Developer ID-signed `Hozz.app` and
`Hozz-<version>-<build>-mac-unnotarized.zip`, without sending the app to Apple.
**Do not publish that ZIP as a notarized beta.** It preserves the archive and
dSYMs and validates the signer, entitlements, runtime, timestamps, and version.
Developer ID packaging never creates a new Developer ID identity automatically.

Notarization sends the packaged application to Apple's notary service and
therefore requires separate explicit authorization:

```bash
# Use an existing Keychain notary credential profile:
export HOZZ_NOTARY_KEYCHAIN_PROFILE='Your existing Hozz notary credentials'
# Alternatively use HOZZ_ASC_KEY_PATH, HOZZ_ASC_KEY_ID, HOZZ_ASC_ISSUER_ID above.
tools/apple-beta.sh notarize mac --distribution developer-id \
  --version 0.1.0 --build 1 --source-commit "$RELEASE_COMMIT" --confirm-notarize
# Add --allow-provisioning-updates only if separately authorized and configured.
```

The lane freshly archives/exports, submits with `notarytool --wait`, and retains
the shared build lease throughout the wait (up to one hour), stapling, signature
checks, `stapler validate`, Gatekeeper assessment, and final ZIP creation.
Only an explicit **Accepted** response with a submission ID can reach stapling.
The final `Hozz-<version>-<build>-mac.zip` contains the stapled app; the lane
extracts it again and verifies the recovered app and ticket. Its SHA-256 is in
`evidence.json`. `notary-submission.json`, the notary service log when available,
verification logs, archive, and dSYMs are retained.

Timeout/failure does not prove the server stopped processing: retain the
submission ID and inspect it with authorized `notarytool info` under the build
lease before retrying. An accepted submission with a stapling or Gatekeeper
failure is **not a completed deliverable**. No upload to a download host, app
launch, or tester distribution occurs in this lane.

### Make the recommended drag-to-Applications DMG

Repackage the **existing notarized ZIP**, without rebuilding or re-signing its
app. This keeps the binary and its source provenance identical to the ZIP beta.
Install the pinned Finder-layout dependencies in an isolated environment once:

```bash
python3 -m venv build/dmg-tools-venv
build/dmg-tools-venv/bin/python -m pip install -r tools/requirements-dmg.txt
export HOZZ_DMG_PYTHON="$PWD/build/dmg-tools-venv/bin/python"

# Set HOZZ_TEAM and the previously authorized notary credentials as above.
# Packaging source must be committed and clean before signing/submitting.
tools/mac-dmg.sh \
  --release-evidence build/apple-beta/YOUR-NOTARIZED-MAC-LANE/evidence.json \
  --source-commit "$(git rev-parse HEAD)" --confirm-notarize
```

The lane holds the same shared Apple build lease from extraction through
notarization, stapling, and read-only mount verification. It checks the input
ZIP's recorded SHA-256, source commit, signing identity, app metadata and
notarization ticket before packaging. It uses the app's existing icon and a
fixed Finder icon layout; no Finder automation, installer scripts, administrator
prompts, or new artwork are involved.

The resulting DMG contains `Hozz.app`, an `Applications` shortcut, and short
installation instructions. The image is Developer ID-signed, independently
notarized, stapled, and Gatekeeper-assessed. Verification mounts only that image
at a unique private path, checks the layout, copies the app to private test
storage, compares app contents/modes/symlinks, and rechecks the copied app's
signature and ticket. It always attempts to unmount its own verification
volume, including on a failed check. It never installs in `/Applications` or
launches Hozz.

Outputs and evidence remain under `build/mac-dmg/`. `evidence.json` records
**both** the original application's commit and the packaging-tool commit,
plus input ZIP and final DMG hashes. Only a successful `distributable: true`
image is ready for a separately approved release upload. Retain the ZIP,
archive, dSYMs, and submission logs; do not move the existing release tag to
pretend the unchanged application was rebuilt.

```bash
python3 -m unittest tools.tests.test_mac_dmg
```

## Explicit upload; no tester distribution

Upload additionally requires Fastlane (validated CLI: 2.238.0), an existing
App Store Connect app record for the selected bundle ID, and an authorized
App Store Connect API key in
[Fastlane's private JSON format](https://docs.fastlane.tools/app-store-connect-api/#using-fastlane-api-key-json-file).
Keep that JSON outside source control. Set `HOZZ_ASC_API_KEY_JSON` to its path;
never paste its contents into a command, log, issue, or this repository.

```bash
export HOZZ_ASC_API_KEY_JSON=/absolute/private/path/hozz-app-store-key.json
# Only after the owner explicitly authorizes this upload:
tools/apple-beta.sh upload ios --version 0.1.0 --build 1 \
  --source-commit "$RELEASE_COMMIT" --confirm-upload
tools/apple-beta.sh upload mac --version 0.1.0 --build 1 \
  --source-commit "$RELEASE_COMMIT" --confirm-upload
```

`RELEASE_COMMIT` must be the exact, approved 40-character source commit.
Signed archives, upload, and notarization refuse a different HEAD, staged/unstaged changes, or
untracked source files. The gate is checked before generation, after generation
and compilation, after export, immediately before submission, and before
successful completion. Only explicit `--candidate` archives and unsigned build
diagnostics may remain dirty. Final archives can use `--source-commit` to
require the approved commit rather than capturing the current clean HEAD.
A later source change cannot silently turn an inspection build into the
approved release.

Each upload invocation archives and validates fresh local output first.
Fastlane uploads that IPA/PKG using the API key and waits up to one hour for
processing while the **outer lease remains held continuously**. No tester
groups are selected, no external distribution is requested, and no public
release is submitted. Existing App Store Connect automatic internal-group
settings can still make an uploaded build available; review them beforehand.

A timeout/error can occur **after Apple received the upload**. Check the exact
version/build in App Store Connect before retrying; do not interpret a failed
lane as proof Apple has no build. `evidence.json` records attempted versus
confirmed upload separately. Archives, symbols, and logs survive failures.

The maintainer still needs to complete beta contact/review details, privacy
policy URL and App Privacy answers, age rating/export-compliance questions,
tester invitations/groups, and (for external testers) Beta App Review. The lane
does not fabricate or change those answers. An empty collected-data manifest
describes today's app: Hozz runs no collection service and sends records only
to user-configured destinations. It is not a substitute for reviewing Apple's
App Privacy questionnaire against actual behavior.

## Privacy manifest basis

First-party dynamic frameworks carry their own manifests rather than relying
on the containing app. Reasons were checked against
[Apple's required-reason documentation](https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api):

| Bundle | Actual use | Reason |
| --- | --- | --- |
| HozzDeliver | `SharedReceiverStore.machineIdentifier`, app-private `UserDefaults.standard` | `CA92.1` |
| HozzStore | `StoreLocation` file attributes within app/group storage | `C617.1` |
| HozzHealth | Export and ZIP file attributes in its private spool | `C617.1` |
| HozzReceive | Archive attributes and timestamps in storage/user-chosen watched folders | `C617.1`, `3B52.1` |
| HozzReceive | Show storage capacity and refuse ingestion below the free-space floor | `85F4.1`, `E174.1` |
| Mac app | Remember the user's watched-folder selection in private defaults | `CA92.1` |

No tracking, advertising, keyboard, or boot-time API reason is asserted.
Main iOS app/widget code does not directly use the listed required-reason APIs;
their manifests remain empty while the frameworks declare their own usage.
Required-reason declarations are not permission to transmit disk diagnostics.
The low-space HTTP response is a generic 507 refusal with no disk measurements;
the actionable byte counts stay in the receiver's local diagnostic event and
storage report. A real-socket regression verifies the exact response body.
