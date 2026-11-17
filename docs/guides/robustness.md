# Robustness sweeps

A sweep runs one model over one scene split on a grid of seeded input perturbations, then scores every cell. Ground truth is not perturbed.

## Steps

1. Write a sweep file. The keys and the cell cap are in [the longer note](../robustness.md) and in [`robustness-sweep.schema.json`](../spec/robustness-sweep.schema.json).
2. Run it. `coperceive robustness sweep --help` takes the sweep file plus the model and scene flags. The longer note's demo invocation is:

    ```bash
    coperceive demo create /data/demo
    coperceive robustness sweep sweep.yaml \
      --manifest tests/fake_model_plugin/sweep-model.yaml \
      --scenes /data/demo/scenes --out /data/sweep --run-id demo
    ```

Numbers from that fake plugin are fixture numbers. They are not a detector's scores.

A single perturbed model run, without a grid, is `coperceive model run ... --perturb`. The help example is `latency_frames=2,pose_noise_xy=0.2,seed=7`.

## More detail

- [Robustness sweeps](../robustness.md)
- [Perturbations](../spec/perturbations.md)
