# SPDX-License-Identifier: Apache-2.0
"""Sensor data in a result bundle: the redistributable allow list.

A bundle always carries the scores, the boxes, the poses and the repro card.
LiDAR points and camera images are only carried when **every** one of these is
true:

1. each included scene is a Coperceive demo scene: its ``COMPLETE.json``
   records ``generator: "coperceive demo"`` **and** the adapter that wrote it,
   ``generic`` (a typed dataset id or a folder name is never proof),
2. the exporter ticks "Include sensor data", and
3. the selected frames' sensor bytes fit the sensor-data cap (50 MiB by
   default), checked before anything is written.

Anything else is omitted and the bundle records why. No third-party dataset is
on the allow list: OPV2V, V2XSet, V2V4Real, DAIR-V2X-C and generic folders are
refused by name, and so is Griffin, whose catalog entry is ``requires_terms:
true`` (each user accepts its terms before downloading, so its frames are not
passed on). Every other dataset id is refused as unknown.
"""

from __future__ import annotations

from dataclasses import dataclass

# The default sensor-data cap: uncompressed LiDAR + camera bytes for the
# included frames. Both export and open use it; Python callers override it with
# ``BundleExportRequest.sensor_max_bytes`` and ``OpenLimits.max_sensor_bytes``.
SENSOR_MAX_BYTES = 50 * 1024 * 1024

# The marker the demo generator writes into COMPLETE.json, and the adapter it
# records. Their bytes come from Apache-2.0 code in this repository.
DEMO_GENERATOR = "coperceive demo"
DEMO_ADAPTER = "generic"
DEMO_LICENCE = "Apache-2.0"

# Names that only a generator marker can vouch for: typed alone, they are refused.
SYNTHETIC_DATASETS: dict[str, str] = {
    # dataset key -> people-facing label
    "demo": "Coperceive demo (synthetic)",
    "fixture": "synthetic fixture",
    "synthetic": "synthetic scene",
    "generated": "generated scene",
}

# Named refusals, so the reason is specific in the export review. Every other
# dataset id is refused as unknown by the default-deny rule below.
REFUSED_DATASETS: dict[str, str] = {
    "opv2v": "OPV2V",
    "v2xset": "V2XSet",
    "v2v4real": "V2V4Real",
    "dairv2xc": "DAIR-V2X-C",
    "generic": "generic datasets",
    "griffin": "Griffin",
}

# Why a named dataset is refused, when there is more to say than "no licence".
REFUSED_BECAUSE: dict[str, str] = {
    "griffin": "the dataset catalog marks Griffin requires_terms: true, so each user "
    "accepts its terms before downloading and its sensor data is never redistributed",
}

# The adapter that ingests each dataset. A local copy matches a bundle only when
# its ``COMPLETE.json`` records the same adapter (plus the scene id and frame
# count); this is how the recipient's library proves it is the same dataset.
DATASET_ADAPTERS: dict[str, str] = {
    "demo": "generic",
    "fixture": "generic",
    "synthetic": "generic",
    "generated": "generic",
    "griffin": "griffin",
    "opv2v": "opv2v",
    "v2xset": "v2xset",
    "v2v4real": "v2v4real",
    "dairv2xc": "dair-v2x-c",
}


def dataset_key(value: str) -> str:
    """Lowercase letters and digits only, so ``V2X-Set`` matches ``v2xset``."""
    return "".join(character for character in value.strip().lower() if character.isalnum())


@dataclass(frozen=True)
class SensorPolicy:
    """Whether one dataset's sensor bytes may travel in a bundle."""

    dataset_id: str | None
    allowed: bool
    licence: str | None = None
    licence_url: str | None = None
    dataset_label: str | None = None
    adapter: str | None = None
    reason: str = ""

    def as_document(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "allowed": self.allowed,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "dataset_label": self.dataset_label,
            "adapter": self.adapter,
            "reason": self.reason,
        }


def _refused(dataset_id: str, reason: str) -> SensorPolicy:
    key = dataset_key(dataset_id)
    return SensorPolicy(
        dataset_id=dataset_id,
        allowed=False,
        adapter=DATASET_ADAPTERS.get(key),
        reason=reason,
    )


def sensor_data_policy(
    dataset_id: str | None,
    *,
    recorded_adapter: str | None = None,
    generator: str | None = None,
) -> SensorPolicy:
    """The explicit allow-list decision for one dataset id.

    Never infers a dataset from a name: a missing id is a refusal, and the
    mapping from id to licence is this module's, not a guess from a scene id.
    """
    if generator == DEMO_GENERATOR and recorded_adapter == DEMO_ADAPTER:
        return SensorPolicy(
            dataset_id="demo",
            allowed=True,
            licence=DEMO_LICENCE,
            dataset_label=SYNTHETIC_DATASETS["demo"],
            adapter=DEMO_ADAPTER,
        )
    if recorded_adapter is not None and recorded_adapter != DEMO_ADAPTER:
        specific = REFUSED_BECAUSE.get(dataset_key(recorded_adapter))
        return _refused(
            dataset_id or "unknown",
            f"the scene records adapter {recorded_adapter!r}, "
            "so it is not a synthetic Coperceive scene" + (f"; {specific}" if specific else ""),
        )
    if dataset_id is None or not dataset_id.strip():
        return SensorPolicy(
            dataset_id=None,
            allowed=False,
            reason="the scene has no Coperceive generator marker, so its licence is unknown",
        )
    key = dataset_key(dataset_id)
    if not key:
        return _refused(dataset_id, f"the dataset id {dataset_id!r} has no letters or digits")
    if key in SYNTHETIC_DATASETS:
        return _refused(
            dataset_id,
            "the scene has no Coperceive generator marker; a typed dataset name is not proof",
        )
    if key in REFUSED_DATASETS:
        name = REFUSED_DATASETS[key]
        return _refused(
            dataset_id,
            REFUSED_BECAUSE.get(key)
            or f"sensor data for {name} is never redistributed "
            "(no redistributable licence is recorded)",
        )
    return _refused(
        dataset_id,
        f"no redistributable licence is recorded for dataset {dataset_id!r}; "
        "only Coperceive demo scenes are on the allow list",
    )


def allow_list_document() -> dict[str, object]:
    """The mapping a reviewer can read, and the export review shows for a dataset."""
    return {
        "allowed": {
            "generator": DEMO_GENERATOR,
            "adapter": DEMO_ADAPTER,
            "licence": DEMO_LICENCE,
        },
        "needs_generator_marker": dict(sorted(SYNTHETIC_DATASETS.items())),
        "refused": dict(sorted(REFUSED_DATASETS.items())),
    }
