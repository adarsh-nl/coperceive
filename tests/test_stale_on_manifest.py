# SPDX-License-Identifier: Apache-2.0
"""A prepared scene whose recorded adapter manifest is not the current one is stale.

Both places that reuse a prepared scene check it: local ingest (``ingest_one`` skips a
complete scene) and the cluster cache (``dataset_status_document`` and a prepare).
A scene prepared by v0.1.1 records the digest of v0.1.1's manifest; the test stands in
for it by writing another digest into ``COMPLETE.json``, as nothing else differs.
"""

from __future__ import annotations

import json
from pathlib import Path

from coperceive.adapters.ingest import (
    COMPLETE,
    MANIFEST_SHA256,
    ingest,
    manifest_changed,
    manifest_sha256,
)
from coperceive.adapters.registry import resolve_adapter
from coperceive.cluster_datasets import (
    MANIFEST_CHANGED,
    dataset_status_document,
    prepare_cluster_dataset,
)

from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood

OLD_DIGEST = "0" * 64


def _raw(root: Path) -> Path:
    write_opencood(root, "test", [two_cav_scene("scene_0001"), two_cav_scene("scene_0002")])
    return root


def _record(scene: Path, digest: str | None) -> None:
    """Rewrite the manifest digest a prepared scene records (``None``: remove it)."""
    marker = scene / COMPLETE
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if digest is None:
        payload.pop(MANIFEST_SHA256, None)
    else:
        payload[MANIFEST_SHA256] = digest
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _recorded(scene: Path) -> object:
    return json.loads((scene / COMPLETE).read_text(encoding="utf-8")).get(MANIFEST_SHA256)


def test_the_current_digest_is_recorded_and_nothing_else_is_changed(tmp_path: Path) -> None:
    adapter = resolve_adapter("opv2v")
    current = manifest_sha256(adapter)
    assert current is not None
    out = tmp_path / "scenes"
    ingest(adapter, _raw(tmp_path / "raw"), out, params={"split": "test"}, workers=1)
    assert _recorded(out / "scene_0001") == current
    assert not manifest_changed(out / "scene_0001", current)
    assert manifest_changed(out / "scene_0001", OLD_DIGEST)
    # An adapter with no manifest file has nothing to compare: never stale.
    assert not manifest_changed(out / "scene_0001", None)


def test_local_ingest_rebuilds_a_scene_prepared_under_another_manifest(tmp_path: Path) -> None:
    adapter = resolve_adapter("opv2v")
    raw = _raw(tmp_path / "raw")
    out = tmp_path / "scenes"
    params = {"split": "test"}
    first = ingest(adapter, raw, out, params=params, workers=1)
    assert sorted(first.written) == ["scene_0001", "scene_0002"]

    again = ingest(adapter, raw, out, params=params, workers=1)
    assert sorted(again.skipped) == ["scene_0001", "scene_0002"]

    _record(out / "scene_0001", OLD_DIGEST)
    rebuilt = ingest(adapter, raw, out, params=params, workers=1)
    assert rebuilt.written == ("scene_0001",)
    assert rebuilt.skipped == ("scene_0002",)
    assert _recorded(out / "scene_0001") == manifest_sha256(adapter)


def test_a_marker_from_before_provenance_is_kept(tmp_path: Path) -> None:
    # No digest recorded: nothing to compare, so local ingest and the cluster cache both
    # keep the scene (tests/test_adapter_ingest.py pins the same for local ingest).
    adapter = resolve_adapter("opv2v")
    out = tmp_path / "scenes"
    ingest(adapter, _raw(tmp_path / "raw"), out, params={"split": "test"}, workers=1)
    _record(out / "scene_0001", None)
    assert not manifest_changed(out / "scene_0001", manifest_sha256(adapter))


def _cache(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "opv2v" / "hash" / "test"


def test_cluster_cache_marks_a_scene_stale_when_the_manifest_changed(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    cache = _cache(tmp_path)
    prepare_cluster_dataset("opv2v", raw / "test", cache)
    before = {s["scene_id"]: s for s in dataset_status_document(cache)["scenes"]}
    assert {s["state"] for s in before.values()} == {"ready"}

    _record(cache / "scene_0001", OLD_DIGEST)
    after = {s["scene_id"]: s for s in dataset_status_document(cache)["scenes"]}
    assert after["scene_0001"]["state"] == "stale"
    assert after["scene_0001"]["reason"] == MANIFEST_CHANGED
    assert after["scene_0002"]["state"] == "ready"
    # Without the refresh the stored states are reported as they are.
    unchanged = dataset_status_document(cache, refresh_stale=False)
    assert {s["state"] for s in unchanged["scenes"]} == {"ready"}
    # A stale scene is still on disk: it can be opened or re-prepared.
    assert (cache / "scene_0001" / COMPLETE).is_file()

    # Re-prepare (the row's action) prepares it again under the current manifest.
    again = prepare_cluster_dataset("opv2v", raw / "test", cache, scenes=["scene_0001"])
    states = {s["scene_id"]: s["state"] for s in again["scenes"]}
    assert states == {"scene_0001": "ready", "scene_0002": "ready"}
    assert _recorded(cache / "scene_0001") == manifest_sha256(resolve_adapter("opv2v"))


def test_a_cluster_prepare_builds_a_stale_scene_again_and_reuses_the_rest(
    tmp_path: Path,
) -> None:
    raw = _raw(tmp_path / "opv2v")
    cache = _cache(tmp_path)
    prepare_cluster_dataset("opv2v", raw / "test", cache)
    _record(cache / "scene_0002", OLD_DIGEST)
    # A prepare of the whole dataset finds it stale and prepares it again; the other
    # scene, prepared under the current manifest, is reused as it is.
    kept = json.loads((cache / "scene_0001" / COMPLETE).read_text(encoding="utf-8"))
    result = prepare_cluster_dataset("opv2v", raw / "test", cache)
    assert {s["state"] for s in result["scenes"]} == {"ready"}
    current = manifest_sha256(resolve_adapter("opv2v"))
    assert _recorded(cache / "scene_0002") == current
    assert json.loads((cache / "scene_0001" / COMPLETE).read_text(encoding="utf-8")) == kept
