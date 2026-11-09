"""Capture-only: add a second simulated run ("baseline") to a demo folder, for the compare view."""

import random
import sys
from dataclasses import replace
from pathlib import Path

import coperceive.demo.generate as g
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import RunMeta, write_run

root = Path(sys.argv[1])
scenes, preds = root / "scenes", root / "predictions"
rng = random.Random(21)
rows = []
for r in g._prediction_rows(scenes, seed=11):
    if rng.random() < 0.22:
        continue  # more misses than the fused run
    c = r.center
    rows.append(
        replace(
            r,
            center=(c[0] + rng.gauss(0, 0.45), c[1] + rng.gauss(0, 0.45), c[2]),
            score=round(0.55 + 0.4 * rng.random(), 4),
        )
    )
meta = RunMeta(
    run_id="baseline",
    model="synthetic baseline",
    score_note="Simulated noisy boxes; not a model result.",
    categories=("car", "cyclist", "pedestrian"),
    sources=(("noisy", "simulated detector"),),
    created_utc="2026-01-01T00:00:00Z",
)
write_run(preds, "baseline", meta, rows)
rep = evaluate_run(scenes, preds, "baseline", DEFAULT_CONFIG, created_utc="2026-01-01T00:00:00Z")
print("errors", rep.errors)
