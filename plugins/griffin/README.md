# coperceive-griffin-model

The Griffin model plugin for `coperceive model run` (ADR 0011, `docs/spec/model-plugin.md`).
It wraps the released Griffin checkpoints (Wang et al., arXiv:2503.06983, MIT) as a
protocol-1 worker that runs in the Griffin conda environment (Python 3.8, torch 1.9.1+cu111,
mmdet3d v0.17.1) and answers frames from the toolkit's `results_nusc_det.json`.

- `model.yaml`: the manifest (models `vehicle-side` and `drone-side`, conda env `griffin`,
  the CUDA modules, the raw files each frame needs, the pinned checkpoint digests).
- `worker.py`: the protocol loop. On the first `configure` of a batch run it copies the
  toolkit into the work directory, trims `scene_infos.json` to the staged scenes, renumbers
  the split, runs the toolkit's converter, info generator and `tools/test.py` once, then
  answers every frame from the detection JSON (`convert.py` holds the conversion rules of
  `scripts/griffin/griffin_to_predictions.py`). With the option `det_json` it answers from
  an existing detection JSON and needs no GPU (what `coperceive model check` uses).
- `stage.py`, `toolkit.py`: the staging and toolkit steps, argv lists only.

Everything under `coperceive_griffin_model/` must run on Python 3.8 (checked by
`tests/test_griffin_model_syntax.py`). On the HPC the wheel is built on the login node and
installed into the coperceive `.venv` from the local wheelhouse; compute nodes install nothing:

```text
pip wheel plugins/griffin --no-deps -w ~/wheelhouse
pip install --no-index --find-links ~/wheelhouse coperceive-griffin-model
```
