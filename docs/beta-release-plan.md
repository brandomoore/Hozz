# First public beta

The first release is a beta for real testers, not a claim that every platform
or health-data type is complete. It combines the reviewed Android/archive work
with the Apple build interlock.

**Published:** signed Android APK and notarized Mac DMG/ZIP in
[beta 1](https://github.com/brandomoore/Hozz/releases/tag/v0.1.0-beta.1).
**Approved for external testing:** iPhone/iPad 0.1.0 (1) through
[TestFlight](https://testflight.apple.com/join/y7K4DAdF). The gates below remain
the checklist for subsequent candidates; a local build alone is not a release.

## Release gates

| Gate | Required outcome |
| --- | --- |
| Candidate integration | Reviewed archive implementation and current build safeguards coexist on one commit. |
| Android distribution | A non-debuggable, consistently signed preview APK; version, source commit, checksum, and installation instructions accompany it. |
| Apple distribution | Reproducible iPhone/iPad TestFlight archives and a signed, notarized drag-to-Applications Mac DMG (ZIP alternative); the release lane retains the Apple build lease throughout archive, export, and processing. |
| Transfer acceptance | Synthetic archives import, retry without duplication, retain unsupported records and deletions, and re-export without losing canonical data. No real health records are test fixtures. |
| Regression checks | Generated contracts, Python and Android tests, repeated Apple tests, and device-architecture builds pass on the integrated candidate. Expected skips are disclosed. |
| Tester guidance | Explain setup, supported formats, known limits, data privacy, and how to report a problem without posting health data. |
| Distribution approval | Verify the exact artifact and signing identity before uploading. Store accounts, beta review, and external tester access are separate from a successful local build. |

## Beta scope

- iPhone/iPad: Apple Health export and user-configured destinations.
- Mac: receive, inspect, and use the local archive.
- Android: import, inspect, and re-export a Hozz archive. Android cannot read
  Apple Health directly; extraction must happen on an Apple device.
- The downloadable Android beta includes experimental, opt-in Health Connect
  writes by the maintainer's explicit choice. Projection is not a prerequisite
  for testing the archive, and APK distribution is not Google Play policy
  approval.
- Clinical extraction stays unavailable. Cumulative activity and ungrouped
  sleep remain archive-only on Android.
- Background delivery is subject to platform scheduling and access limits.
  A beta is not a medical device, medical advice, or a substitute for the
  original health store or a verified backup.

The Mac beta uses Developer ID distribution rather than making the existing
standalone assistant executable fit Mac App Store sandbox rules. Do not change
the assistant's archive access merely to silence a distribution check.

## Publication record

For each candidate, retain the source commit, artifact checksums, signing
verification, test results, and known unverified paths outside the repository.
Do not commit credentials, health archives, provisioning profiles, private
keys, screenshots containing health values, or generated Xcode projects.

Publish only the channels actually ready: a signed APK download is not a
Google Play release, an uploaded Apple build is not an approved public
TestFlight invitation, and an unsigned Mac app is not a notarized download.
