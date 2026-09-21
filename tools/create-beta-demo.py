#!/usr/bin/env python3
"""Create a deterministic, archive-only synthetic Hozz beta fixture."""

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


NAMESPACE = UUID("b0ca063f-0b89-4ddb-a937-986d8ba64484")
STEP_COUNT = 240
CREATED_AT = "2026-01-02T00:00:00Z"


def timestamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def base_record(label, source_type, canonical_type, kind):
    record_id = str(uuid5(NAMESPACE, label))
    return {
        "schemaVersion": 1,
        "id": record_id,
        "canonicalId": f"apple.healthkit:{record_id}",
        "canonicalType": canonical_type,
        "recordVersion": 1,
        "kind": kind,
        "type": source_type,
        "sourceRecord": {
            "store": "apple.healthkit",
            "id": record_id,
            "type": source_type,
        },
        "lineage": [{"store": "apple.healthkit", "recordId": record_id}],
        "source": {
            "name": "Hozz synthetic beta demo - not personal health data",
            "bundleIdentifier": "com.thatcube.hozz.synthetic",
        },
        "metadata": {"hozzSyntheticDemo": True},
    }


def records():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(STEP_COUNT):
        record = base_record(
            f"steps-{index}", "HKQuantityTypeIdentifierStepCount",
            "activity.steps", "quantity",
        )
        record["startDate"] = timestamp(start + timedelta(minutes=index))
        record["endDate"] = timestamp(start + timedelta(minutes=index + 1))
        record["quantity"] = {"value": index + 1, "unit": "count", "count": 1}
        yield record

    sleep = base_record(
        "sleep", "HKCategoryTypeIdentifierSleepAnalysis", "sleep.stage", "category",
    )
    sleep.update({
        "startDate": "2026-01-01T22:00:00Z",
        "endDate": CREATED_AT,
        "value": 3,
    })
    yield sleep

    deletion = base_record(
        "already-deleted-step", "HKQuantityTypeIdentifierStepCount",
        "activity.steps", "deletion",
    )
    deletion.update({"recordVersion": 2, "deleted": True})
    # Deletion records carry identity, not the erased source's sample fields.
    del deletion["source"]
    del deletion["metadata"]
    yield deletion


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_demo(path):
    fixture = list(records())
    payload = b"".join(json_bytes(record) + b"\n" for record in fixture)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "schemaVersion": 1,
        "archiveId": digest,
        "format": "hozz-ndjson",
        "recordSchema": "hozz/v1/canonical-record",
        "recordsEntry": "hozz-synthetic-demo.ndjson",
        "createdAt": CREATED_AT,
        "recordCount": len(fixture),
        "sourcePlatform": "Synthetic beta fixture (not a real HealthKit export)",
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output, ZipFile(output, "w") as archive:
        for name, content in (
            ("hozz-manifest.json", json_bytes(manifest)),
            (manifest["recordsEntry"], payload),
        ):
            entry = ZipInfo(name, date_time=(2026, 1, 2, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, content)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New .zip path; never overwrites")
    arguments = parser.parse_args()
    if arguments.output.suffix.lower() != ".zip":
        parser.error("output must be a .zip file")
    try:
        manifest = write_demo(arguments.output)
    except OSError as error:
        parser.exit(1, f"Could not write synthetic demo: {error}\n")
    print(f"Created synthetic demo: {manifest['recordCount']} canonical records")
    print(f"SHA-256: {hashlib.sha256(arguments.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
