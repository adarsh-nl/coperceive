# Perturbations

A run can delay, drop, or jitter the messages of every agent except the ego, and it can keep only the first N agents. The ego is the first agent in scene order among the agents the run includes. When no list is passed, that is the first agent in the scene, the same reference agent the dataset index uses. Ground truth is not modified. Scoring stays coperceive's own match against the scene annotations.

## Spec

`coperceive model run ... --perturb latency_frames=2,pose_noise_xy=0.2,seed=7`

`coperceive model perturb-inputs --scenes DIR --scene ID --perturb SPEC --out DIR` writes the same inputs for a script that is not a plugin. `perturbation.json` is the spec. `frames.json` lists, per ego pose time, which agents are present, whether their message arrived, the pose time the message was taken from, and the pose that was sent. `ground_truth` in that file is a copy of the scene annotations.

The same object is stored in `run.json` `provenance.perturbation`, in `<run>/perturbation.json`, and under `<run>/perturbed/<scene>/`. The predictions panel shows it for the selected run.

## Order

For each frame the generator is `random.Random` seeded by the 8-byte BLAKE2s of `seed|scene_id|time_us`. Draws do not depend on other frames.

1. **Agent dropout.** Each non-ego agent draws one uniform in `[0, 1)` and is removed when the draw is below `agent_dropout`.
2. **`max_agents`.** This count includes the ego. Remaining agents keep scene order, so the ego and the earliest survivors stay.
3. **Latency.** `latency_frames` steps back along that agent's own pose times from the latest pose at or before the query. `latency_ms` keeps the latest pose at or before `query - latency_ms`. If both are set, the earlier of the two is used. No earlier pose removes the agent. The current pose is not reused and nothing is extrapolated.
4. **Packet loss.** Each agent that still has a message draws one uniform and the message is dropped when the draw is below `packet_loss`.
5. **Pose noise.** `pose_noise_xy` is the standard deviation in metres on world x and y. `pose_noise_yaw` is the standard deviation in degrees about world z, applied on the left of the rotation. Each set field consumes Box-Muller draws from `random.Random.random`, not `random.gauss`. A null field draws nothing.

## What published work measures

These are the axes cooperative-perception papers report, not their code or their configs:

- Xu et al., "OPV2V: An Open Benchmark Dataset and Fusion Pipeline for Perception with Vehicle-to-Vehicle Communication", ICRA 2022. Localization noise and communication delay.
- Xu et al., "V2X-ViT: Vehicle-to-Everything Cooperative Perception with Vision Transformer", ECCV 2022. Time delay and localization error.
- Yu et al., "DAIR-V2X: A Large-Scale Dataset for Vehicle-Infrastructure Cooperative 3D Object Detection", CVPR 2022. Temporal offset between vehicle and infrastructure.
- Hu et al., "Where2comm: Communication-Efficient Collaborative Perception via Spatial Confidence Maps", NeurIPS 2022. How many agents' messages are worth sending.

coperceive does not reuse those implementations. The definitions above are the ones the tests check.
