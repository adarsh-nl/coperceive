# Griffin-25m detection run from the released checkpoints — plan (Task 12, draft)

> Draft for Adarsh's review before anything is installed, cloned or submitted. Prepared read-only on 2026-10-17 (session lead plus one research agent). The coperceive API names in §4 (`BoxFrame.WORLD`, `write_run(root, run_id, meta, rows, *, force=False)`, `validate_boxes(scene, meta, boxes)`, `RunMeta`, `PredictionBox`) were checked against `feat/eval-step-a` after the draft and are correct; the converter script itself does not exist yet. The sbatch draft is `~/scripts/slurm/griffin-infer.sbatch` on the HPC (not in git, not submitted).

Two notes from the lead's read of the sbatch: `source activate` inside a job needs conda's profile script first (`source /software/anaconda3/2025.06/etc/profile.d/conda.sh`) since modules do not inherit from the login node; and the install job should be written as its own sbatch with the same module loads and `TORCH_CUDA_ARCH_LIST` before any inference job exists.

Status: draft for Adarsh. Nothing has been installed, cloned, created or
submitted. Every fact below names the command or URL it came from and quotes
the output. Items I could not establish say "not found".

Headline correction to the task premise: **the Griffin baseline models are
camera-only.** The vehicle-side config says `use_lidar=False, use_camera=True`
(training log and repo config, quoted in A.6). A job that stages the 25 m
subset "excluding camera/" cannot run these checkpoints; the job must stage
the four RGB vehicle cameras (~69 GB) and may skip `lidar/` and `instance_*/`.

---

## 0. Facts established (A–F)

### A. Griffin toolkit (GitHub `wang-jh18-SVM/Griffin`)

**A.1 Licence.** `curl https://raw.githubusercontent.com/wang-jh18-SVM/Griffin/main/LICENSE.txt`
(the file is `LICENSE.txt`; `LICENSE` returns 404):

> MIT License
> Copyright (c) [2025] [Jiahao Wang]

README badge: `[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)`.

**A.2 Pinned stack** — `docs/Installation.md` (raw.githubusercontent.com), quoted:

> | **CUDA** | Version 11.X |
> | **Python** | 3.8 (managed via Conda) |
> conda create -n griffin python=3.8 -y
> pip install torch==1.9.1+cu111 torchvision==0.10.1+cu111 -f https://download.pytorch.org/whl/torch_stable.html
> export CUDA_HOME=/usr/local/cuda-11.8/  # Adjust path to your CUDA installation
> pip install mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu111/torch1.9.0/index.html
> pip install mmdet==2.14.0 mmsegmentation==0.14.1
> git clone https://github.com/open-mmlab/mmdetection3d.git ; cd mmdetection3d ; git checkout v0.17.1 ; pip install -v -e .
> git clone https://github.com/wang-jh18-SVM/Griffin.git griffin ; cd griffin ; pip install -r requirements.txt

`requirements.txt` (raw): `motmetrics==1.1.3 einops==0.4.1 yapf==0.40.1 setuptools==59.5.0 pillow==9.0.0 tensorboard==2.12.0 wandb`.
`example.yaml` (raw, head) pins `python=3.8.20=he870216_0` and a full pip list (black, filterpy, ...). No `environment.yml`, no `setup.py` in the repo (GitHub tree API, recursive: 317 entries, `truncated: False`; the only `.yml/.yaml/requirements` blobs are `example.yaml`, `projects/ab3dmot_plugin/configs/KITTI.yml`, `requirements.txt`).

The training log on this machine confirms the exact versions the checkpoints were produced with (`head -c 6000 .../logs/vehicle-side-train.log`):

> Python: 3.8.20 ... PyTorch: 1.9.1+cu111 ... CUDA Runtime 11.1 ... CuDNN 8.0.5
> NVCC: Build cuda_11.8.r11.8/compiler.31833905_0
> TorchVision: 0.10.1+cu111 ... MMCV: 1.4.0 ... MMCV CUDA Compiler: 11.1
> MMDetection: 2.14.0 ... MMSegmentation: 0.14.1 ... MMDetection3D: 0.17.1+25e2f21
> NVCC architecture flags: -gencode;arch=compute_37,code=sm_37;...;-gencode;arch=compute_80,code=sm_80;-gencode;arch=compute_86,code=sm_86
> GPU 0,1,2,3: NVIDIA GeForce RTX 4090

**A.3 Custom CUDA ops.** The Griffin repo itself has **no** `.cu/.cpp/setup.py` (tree scan above). `projects/mmdet3d_plugin/agile/modules/multi_scale_deformable_attn_function.py` is a Python wrapper over mmcv's op. Compiled CUDA comes from (i) the prebuilt `mmcv-full==1.4.0` cu111/torch1.9.0 wheel and (ii) `mmdetection3d v0.17.1` installed with `pip install -v -e .`, which compiles its ops (needs `CUDA_HOME` and an nvcc). Artifacts still exist: `curl -sI` of `torch-1.9.1+cu111-cp38` wheel → `HTTP/2 200 content-length: 2041339783`; torchvision 0.10.1+cu111 cp38 → `200`; `mmcv_full-1.4.0-cp38-cp38-manylinux1_x86_64.whl` is listed in the cu111/torch1.9.0 index; mmdetection3d tag `refs/tags/v0.17.1` = `f1107977dfd26155fc1f83779ee6535d2468f449`.

**A.4 Entry point.** `tools/dist_eval.sh` (raw):

> PYTHONPATH="$(dirname $0)/..":$PYTHONPATH python -m torch.distributed.launch --nproc_per_node=$GPUS_PER_NODE --master_port=$MASTER_PORT $(dirname "$0")/test.py $CFG $CKPT --launcher pytorch ${@:4} --eval bbox --show-dir ${WORK_DIR} --out ${WORK_DIR}results-$T.pkl

`tools/test.py` (raw): `--out` must end in `.pkl` (`mmcv.dump(outputs, args.out)`); then `kwargs['jsonfile_prefix'] = osp.join(args.show_dir, "json_output", time.ctime()...)` and `dataset.evaluate(outputs, ...)`. `docs/Training_and_Evaluation.md`: `./tools/dist_eval.sh CONFIG_FILE_VEHICLE CHECKPOINT_VEHICLE 1`.

**A.5 Output format.** `projects/mmdet3d_plugin/datasets/griffin_dataset.py` `_format_bbox` writes `results_nusc.json` (tracking output) and `_format_bbox_det` writes `results_nusc_det.json` (detection output). Per box (quoted):

> nusc_anno = dict(sample_token=sample_token, translation=box.center.tolist(), size=box.wlh.tolist(), rotation=box.orientation.elements.tolist(), velocity=box.velocity[:2].tolist(), detection_name=name, detection_score=box.score, attribute_name=attr, tracking_name=name, tracking_score=box.score, tracking_id=box.token, ...)

Frame: `lidar_nusc_box_to_global` in `datasets/data_utils/data_utils.py`: "Move box to ego vehicle coord system ... box.rotate(Quaternion(info['lidar2ego_rotation'])) ... filter det in ego ... radius > det_range: continue ... Move box to global coord system ... box.rotate(Quaternion(info['ego2global_rotation'])); box.translate(info['ego2global_translation'])" — so the JSON boxes are in the **global (world/ENU) frame**, after a per-class 50 m radius cut (`class_range = dict(car=50, bicycle=50, pedestrian=50)`). `size` is nuScenes **w, l, h** order. `rotation` is a pyquaternion `[w, x, y, z]`; `output_to_nusc_box` builds it as `Quaternion(axis=[0,0,1], radians=-yaw - pi/2)` from the LiDAR-frame box yaw. `box.token` = `track_ids` for the tracking JSON. The eval log confirms the files: "Results writes to .../json_output/Fri_Feb_21_10_36_10_2025/results_nusc.json" and "... results_nusc_det.json", "Found detections for 1490 samples."

**A.6 Configs ↔ models** (GitHub contents API, `projects/configs_griffin_50scenes_25m/`):

| Model | Config | Checkpoint on disk |
|---|---|---|
| vehicle-side single-agent | `vehicle-side/tiny_track_r50_stream_bs8_48epoch_3cls.py` | `vehicle-side/iter_33024.pth` |
| drone-side single-agent | `drone-side/tiny_track_r50_stream_bs8_48epoch_3cls.py` (plus `..._24epoch_3cls_eval.py` / `_eval_train.py` to dump drone queries) | `drone-side/iter_33024.pth` |
| cooperative, instance fusion | `cooperative/instance_fusion/tiny_track_r50_stream_bs8_48epoch_3cls.py` | `cooperative/instance_fusion/iter_33024.pth` |
| early fusion | `early-fusion/tiny_track_r50_stream_bs8_48epoch_3cls.py` | `early-fusion/iter_33024.pth` |
| late fusion | `cooperative/late_fusion/tiny_track_r50_stream_bs1_3cls_late_fusion.py` (+`_ab3dmot`) | (uses the two single-agent checkpoints; `tools/eval_late_fusion.sh`) |

Vehicle-side config (raw), quoted lines:

> input_modality = dict(use_lidar=False, use_camera=True, use_radar=False, use_map=False, use_external=True)
> dataset_prefix = "griffin_50scenes_25m" ; v2x_side = "vehicle-side" ; num_cams = 4
> data_root = f"./datasets/{dataset_prefix}/griffin-nuscenes/{v2x_side}/"
> info_root = f"./data/infos/{dataset_prefix}/{v2x_side}/" ; ann_file_val = info_root + "griffin_infos_val.pkl"
> test=dict(... ann_file=ann_file_val,  # Todo: temp: use val for test ... eval_mod=['det', 'track'] ...)
> load_from = "ckpts/bevformer_tiny_epoch_24.pth"
> score_thresh=0.4, filter_score_thresh=0.35

Model type is `UniAD` (ResNet-50 + BEVFormer-tiny, 50×50 BEV, `point_cloud_range=[-51.2,-51.2,-5,51.2,51.2,3]`, images scaled ×0.5), classes `['car','bicycle','pedestrian']`.

**A.7 Dataset layout the test script reads.** `docs/Dataset_Preparation.md`: the toolkit does **not** read `griffin-release` directly. `bash tools/griffin_converter.sh griffin_50scenes_25m` runs `trans_kitti2nuscenes.py --source_dir datasets/<p>/griffin-release --target_dir datasets/<p>/griffin-nuscenes --split_file data/split_datas/<p>.json --early_fusion` then `generate_nuscenes_pkl.py --root_path datasets/<p>/griffin-nuscenes --out_path data/infos/<p> ... --delay` ("~5-15 minutes per subset"). `trans_kitti2nuscenes.py` `copy_sensor_data` **symlinks** camera dirs (`os.symlink(source_sensor_dir, target_sensor_dir)` into `samples/CAM_FRONT` etc.) and never opens LiDAR files (only `calib/lidar_top.json`; `'num_lidar_pts': 1` is hard-coded). Tokens are plain strings (`_generate_token` returns `base`): `sample_token = 'sample_{timestamp}'` with `timestamp = µs since epoch of 2025-01-01 09:00:00 + frame_id*0.1 s`; scene names become `f'scene-{scene_num:04}-{scene_info["name"]}'`. The ego pose is `R.from_euler('xyz', [roll, pitch, yaw], degrees=True)` (`space_utils.py`), identical to coperceive's `zyx intrinsic` (`griffin.yaml`), so "global" here is the same ENU world as coperceive's.

Val split (`data/split_datas/griffin_50scenes_25m.json`): 37 train / 10 val scenes; val = `scene-0000-Town03-000, scene-0006-Town03-006, scene-0007-Town03-007, scene-0010-Town03-011, scene-0020-Town06-007, scene-0032-Town07-011, scene-0033-Town07-012, scene-0034-Town07-013, scene-0044-Town10HD-012, scene-0046-Town10HD-014`; test = []. 10 × 149 = 1490 samples, matching the eval log.

### B. Checkpoints on this machine

`ls -la /datasets/eemcs/ps/cv/huggingface/griffin/ckpts/griffin_50scenes_25m/`:

```
cooperative/instance_fusion/iter_33024.pth   222769163
cooperative.pth                              222768971
drone-side/iter_33024.pth                    219802531
drone-side.pth                               219802339
early-fusion/iter_33024.pth                  219814563
vehicle-side/iter_33024.pth                  219799203
vehicle-side.pth                             219799075
logs/  cooperative-eval.log cooperative-train.log drone-side-eval.log drone-side-train.log no-fusion-eval.log vehicle-side-eval.log vehicle-side-train.log
```

Also `/datasets/eemcs/ps/cv/huggingface/griffin/ckpts/bevformer_tiny_epoch_24.pth` (400785466 B; the `load_from` base, not needed for inference) and the 40m/55m/random checkpoint folders. **No config `.py/.yaml` sits beside the checkpoints**; configs come from the repo. The `*.pth` at the top level differ by a few hundred bytes from the `iter_33024.pth` files — probably re-saved copies; md5 against `docs/Training_and_Evaluation.md` (`vehicle-side/iter_33024.pth md5 1201f5692e390a75e5b5ab0efa0cae19`) was **not run** (would exceed the login-node budget on 220 MB over NFS? it would not, but I left it for the job). The training-log environment block is quoted in A.2. The vehicle-side eval log reports `mAP: 0.1682`, `NDS: 0.1991`, `AMOTA 0.160`, car AP@2.0 m 0.5197, and the inference pass itself took `1490/1490, 7.7 task/s, elapsed: 194s` on an RTX 4090.

### C. Cluster GPU partition

`sinfo -p main-gpu -o "%N %G %f %m"`:

```
ctit086 gpu:ampere:4 intel,avx,avx2,avx512,tesla,a40 120000
hpc-node03 gpu:lovelace:4 intel,avx,avx2,avx512,tesla,l40 250000
hpc-node17 gpu:lovelace:1 amd,avx,avx2,avx512,tesla,l40s 257295
ctit[084-085] gpu:turing:4(S:0-1) intel,avx,avx2,avx512,quadro,rtx-6000 250000
ctit088 gpu:turing:2(S:0),gpu:ampere:1(S:1) intel,avx,avx2,avx512,geforce,rtx-2080ti,a4500 250000
ctit[090-094] gpu:ampere:4(S:0-1) intel,avx,avx2,avx512,tesla,a40 250000
hpc-node[01-02,04-08] gpu:lovelace:4(S:0-1) intel,avx,avx2,avx512,tesla,l40 250000
hpc-node[09-12] gpu:lovelace:4(S:0-1) intel,avx,avx2,avx512,tesla,l40s 257431
hpc-node14 gpu:lovelace:3(S:0) amd,avx,avx2,avx512,tesla,l40 257295
hpc-node[16,18] gpu:lovelace:1(S:0) amd,avx,avx2,avx512,tesla,l40s 257295
ctit089 gpu:ampere:4(S:0-1) intel,avx,avx2,avx512,tesla,a100 250000
```

`sinfo -p ps` (the PS partition): `hpc-node14 gpu:lovelace:3 ... l40`, `hpc-node[11-12] gpu:lovelace:4 ... l40s`.
`scontrol show node hpc-node01 | grep -E "Gres|Features|CfgTRES"`: `AvailableFeatures=intel,avx,avx2,avx512,tesla,l40  Gres=gpu:lovelace:4(S:0-1)  CfgTRES=cpu=64,mem=250000M,billing=64,gres/gpu=4,gres/gpu:lovelace=4  OS=Linux 5.15.0-176-generic`. ctit089: `tesla,a100  Gres=gpu:ampere:4`.

`module avail 2>&1 | grep -iE "cuda|nvidia|cudnn"` (CUDA-relevant subset): `nvidia/cuda-11.0 11.1 11.2 11.3 11.5 11.6 11.7 11.8 12.4`, `nvidia/cuda-11.x_cudnn-8.6`, `nvidia/cuda-11.x_cudnn-8.8`, `nvidia/cuda-11.1_cudnn-8.2`, `nvidia/cuda-11.8_nccl-2.16`, plus `nvidia/nvhpc*`, `obsolete/nVidia/*`. `module show nvidia/cuda-11.1` → `CUDA_HOME /deepstore/software/nvidia/cuda-11.1`; `nvidia/cuda-11.8` → `/deepstore/software/nvidia/cuda-11.8`. Login-node `gcc --version` = `gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0`; module `gnu/gcc-7` exists.

**Driver version.** No `Driver Version` header anywhere in `~/logs`; found via the apf-attention jobs, which print `nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader`:
`grep -h -m1 ... /home/nanjaiyalathaa/apf-attention/runs/slurm/*.out | sort | uniq -c` →

> 148 NVIDIA L40S, 595.58.03, 46068 MiB

So the L40S nodes run driver **595.58.03** (Oct 2026 jobs). Not established for the A40/A100/L40 nodes (no log found; "not found").

**CUDA support of that driver** — NVIDIA CUDA Toolkit release notes, Table "Corresponding Driver Versions" (https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html): `CUDA 11.1 GA | >=455.23`, `CUDA 11.1.1 Update 1 | >=455.32`, `CUDA 11.8 GA | >=520.61.05`, `CUDA 12.4 GA | >=550.54.14`, `CUDA 12.9 Update 1 | >=575.57.08`. Minor-version-compatibility table: `11.x | >= 450 | < 525`, `12.x | >= 525 | < 580`. Backward compatibility (https://docs.nvidia.com/deploy/cuda-compatibility/why-cuda-compatibility.html): "Backwards compatibility ensures that a newer NVIDIA driver can be used with an older CUDA Toolkit." Compute capability (https://developer.nvidia.com/cuda-gpus): L40 8.9, L40S 8.9, A40 8.6, A100 8.0, Quadro RTX 6000 7.5, RTX 2080 Ti 7.5. nvcc docs (https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/, §5.1): "code compiled for the target sm_80 will run on all other CC 8.x GPUs, such as sm_86 or sm_89" and "Binary compatibility of GPU applications is not guaranteed across different major compute capabilities."

### D. Existing environments

`conda env list`: `cosdh, dhd_env, opencood-official, thesis, v2xvit-official` under `/home/nanjaiyalathaa/.conda/envs/`, base `/software/anaconda3/2025.06`.
`<env>/bin/python -c "import sys,torch; print(sys.version.split()[0], torch.__version__, torch.version.cuda)"` and `ls site-packages | grep -iE "^(torch|mmcv|mmdet|mmdet3d|mmengine|spconv|mmseg)"`:

| env | Python | torch | CUDA | mm* packages |
|---|---|---|---|---|
| opencood-official | 3.7.16 | 1.12.1+cu113 | 11.3 | none (spconv_cu113 2.3.6) |
| v2xvit-official | 3.7.16 | 1.12.1+cu113 | 11.3 | none (spconv_cu113) |
| cosdh | 3.7.16 | 1.12.1+cu113 | 11.3 | none (spconv_cu113) |
| thesis | 3.10.20 | 2.5.1 | 11.8 | none |
| dhd_env | 3.8.20 | 2.0.1+cu118 | 11.8 | none |

**None matches the pins** (torch 1.9.1+cu111, mmcv-full 1.4.0, mmdet 2.14.0, mmdet3d 0.17.1, Python 3.8). No env has any OpenMMLab package. A new env is required.

### E. Disk and where an env could live

`quota -s` printed nothing. `df -h /home/nanjaiyalathaa` → `hpc-storage8:/storage8/home/nanjaiyalathaa 1.0T 99G 926G 10%`. `/datasets/eemcs` → `84T 74T 11T 88%`. `du -sh ~/.conda/envs/cosdh` = `8.6G` in 20 s (full `du ~/.conda` skipped per the budget). Estimated new env: torch wheel alone is 2.04 GB download (~4 GB installed), mmcv-full ~0.5 GB, mmdet3d build + deps ~1 GB → **~6–8 GB**.

Options and what `/home/nanjaiyalathaa/CLAUDE.md` says:
- `~/.conda/envs/griffin` (home): "Code, configs, small outputs. NOT for job scratch or bulk data." An env is code; the five existing envs live here; 926 GB free. **Recommended.** Rule: "Don't `pip install` into the conda `base` env. Use a project env."
- `/datasets/eemcs/ps/cv/huggingface/griffin/...` (owned by me): CLAUDE.md lists `/datasets/eemcs/ps/cv/` as "Shared CV datasets ... Read-mostly"; filesystem is 88 % full. Not for a Python env.
- `/local/$SLURM_JOB_ID`: "Per-job node-local scratch. Wiped after the job." Fine for the mmdet3d *build* and all data, not for a persistent env.
- Compilation of mmdet3d ops needs a GPU-node-like nvcc; the login-node rule ("<1 min, single-core") forbids building there, so the **install itself must be an sbatch job** (CPU or GPU partition; nvcc does not need a GPU to compile).

### F. The 25 m dataset on disk

`/datasets/eemcs/ps/cv/huggingface/griffin/datasets/griffin_50scenes_25m/griffin_50scenes_25m/griffin-release/`: `vehicle-side/{calib,camera,label,lidar,pose,scene_infos.json}`, `drone-side/{calib,camera,label,pose,scene_infos.json}`. No `cooperative/`, no `griffin-nuscenes/`, no `data/infos` → the toolkit's conversion + info-pkl step **has not been done** and must run inside the job (on `/local`).
- `vehicle-side/lidar/lidar_top/` holds **7000 `.ply`** (`format ascii 1.0`, `x y z I`), not the `.bin` the docs mention. Irrelevant for these camera-only models; the converter never opens LiDAR files.
- `vehicle-side/camera/{front,back,left,right,instance_*}`; 7000 PNGs per camera; `ls -l | awk` totals: front 17.2 GB, back 18.0 GB, left 16.5 GB, right 17.2 GB → **~69 GB** for the four RGB cameras; `instance_*` are ~90 KB each (not needed). `label` 18 M, `pose` 7.6 M, `calib` 8.5 K, `lidar` 582 M. 47 scenes (`scene_infos.json`), 149 frames in the first; scene ids like `Town03-000`. Drone cameras ~20 GB each, not needed for vehicle-side.
- HF mirror (`huggingface.co/api/datasets/wjh-svm/Griffin/tree/main/datasets/griffin_50scenes_25m`) ships only zips of exactly these raw folders (`vehicle_camera_*.zip`, `vehicle_lidar.zip 214487013`, `vehicle_metadata.zip`, drone zips, `md5.txt`); no nuScenes-format or infos files are published, so the conversion is unavoidable.

---

## 1. Licence statement

Toolkit (code, configs, converters): MIT, "Copyright (c) [2025] [Jiahao Wang]" (`LICENSE.txt`, quoted in A.1). Checkpoints and dataset: the HF dataset card `README.md` front-matter says `license: mit` (fetched from `huggingface.co/datasets/wjh-svm/Griffin/resolve/main/README.md`); the checkpoints are distributed inside that dataset repo (`ckpts/`), so the same MIT notice covers them. No separate model licence file exists in the repo or the HF tree. Attribution to carry in `run.json` `notes` and in `THIRD_PARTY_NOTICES.md`: the arXiv citation `Wang et al., "Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark", arXiv:2503.06983`.

## 2. Pinned stack and the install I would run (not run)

All of this inside an sbatch job (`ps,main-cpu` is enough for the build; nvcc compiles without a GPU), never on the login node:

```bash
module load nvidia/cuda-11.8 nvidia/cuda-11.x_cudnn-8.6      # CUDA_HOME=/deepstore/software/nvidia/cuda-11.8 (the authors built with nvcc 11.8 against torch cu111)  # TODO: verify gcc version accepted by nvcc 11.8 on the node (gcc 11.4 on login; gnu/gcc-7 module available)
conda create -p "$HOME/.conda/envs/griffin" python=3.8 -y
source activate "$HOME/.conda/envs/griffin"
pip install torch==1.9.1+cu111 torchvision==0.10.1+cu111 -f https://download.pytorch.org/whl/torch_stable.html
pip install mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu111/torch1.9.0/index.html
pip install mmdet==2.14.0 mmsegmentation==0.14.1
# build mmdet3d in node scratch, install editable into the env  (TODO: verify: editable install must point at a persistent path, so clone under $HOME/src, not /local)
git clone https://github.com/open-mmlab/mmdetection3d.git "$HOME/src/mmdetection3d" && cd "$HOME/src/mmdetection3d" && git checkout v0.17.1 && pip install -v -e .
git clone https://github.com/wang-jh18-SVM/Griffin.git "$HOME/src/griffin" && cd "$HOME/src/griffin" && pip install -r requirements.txt
pip install "numpy<1.24" "scipy<1.11" pyquaternion nuscenes-devkit==1.1.9 scikit-learn opencv-python-headless   # TODO: verify exact pins from example.yaml
```

Install location: `~/.conda/envs/griffin` (~6–8 GB; see E). Sources: `~/src/{mmdetection3d,griffin}` (code, small). Nothing is written to `/datasets`.

## 3. Driver / CUDA compatibility verdict

- Driver on L40S nodes: **595.58.03** (apf logs). CUDA 11.1 needs `>=455.23`, CUDA 11.8 needs `>=520.61.05` (release-notes table). NVIDIA: "a newer NVIDIA driver can be used with an older CUDA Toolkit." → the torch cu111 runtime and the cu111 mmcv wheel will load on these nodes. **Verdict: driver is not a blocker.**
- GPU architecture is the real risk. torch 1.9.1+cu111 and mmcv-full 1.4.0 cu111 ship SASS up to `sm_86` with no `sm_89` (A.2 nvcc flags). nvcc docs: "code compiled for the target sm_80 will run on all other CC 8.x GPUs, such as sm_86 or sm_89", so the sm_86 cubins should execute on L40/L40S (8.9). But CUDA 11.1 predates Ada; cuBLAS/cuDNN 8.0.5 bundled with the wheel have no Ada-tuned kernels and this combination is untested here. The cleanest path is to **pin the job to A40 (8.6) or A100 (8.0) nodes** with `--constraint=a40` (or `a100`), which are exactly the architectures the authors' build targets. Driver on those nodes: not found (TODO: verify from the job's own `nvidia-smi` line). Fallback if A40/A100 queues are long: try L40S and watch for `no kernel image is available` errors.
- Turing nodes (rtx-6000, 2080ti; 7.5) are also inside the compiled list but have less memory; not needed.
- mmdet3d ops compiled in the install job must be built with `TORCH_CUDA_ARCH_LIST="8.0;8.6"` (TODO: verify) so they match whichever node class is chosen; building on a CPU node is fine because nvcc does not need a GPU.

## 4. Inference command and mapping to coperceive predictions format 1

Inference (inside the job, cwd = Griffin repo, data staged under `/local`):

```bash
bash tools/dist_eval.sh projects/configs_griffin_50scenes_25m/vehicle-side/tiny_track_r50_stream_bs8_48epoch_3cls.py \
     "$SCRATCH/ckpts/griffin_50scenes_25m/vehicle-side/iter_33024.pth" 1
```

It writes `projects/work_dirs_griffin_50scenes_25m/vehicle-side/tiny_track_r50_stream_bs8_48epoch_3cls/{results-<T>.pkl, json_output/<ctime>/results_nusc.json, results_nusc_det.json, det/, track/}` and prints the nuScenes-style mAP/NDS and AMOTA. Expected on the 10 val scenes: ~1490 samples, ~3–4 min of GPU time on a 4090 (`elapsed: 194s`), plus ~10–15 min conversion and the staging copy of ~69 GB.

Mapping `results_nusc_det.json` → `boxes.parquet` (spec `docs/spec/predictions.md`, `write_run(root, run_id, meta, rows)` in `coperceive/evaluation/predictions.py`, `RunMeta(run_id, model, score_note, categories, sources, created_utc, notes)`, `PredictionBox(scene_id, agent_id, source, time_us, category, score, track_id, center, size_lwh, box_from_rpy: SE3, frame: BoxFrame)`):

| Griffin JSON | coperceive column | note |
|---|---|---|
| sample_token `sample_<ts>` → `sample.json` → scene `scene-NNNN-<name>` | `scene_id` = `<name>` (e.g. `Town03-000`) | coperceive `griffin.yaml`: `scene_id: name` |
| — | `agent_id` = `vehicle` | `griffin.yaml agents: {id: vehicle, root: vehicle-side}` |
| — | `source` = `single` | conventional name in the spec |
| `ts` → frame_id = round((ts − µs(2025-01-01 09:00:00)) / 100000) | `time_us` = frame_id × 100000 | `griffin.yaml scale_us: 100000`; pose json `timestamp` == frame id |
| `detection_name` | `category` | same vocabulary `car/bicycle/pedestrian` |
| `detection_score` | `score` | sigmoid class score after the model's `score_thresh=0.4` → `score_note` must say so |
| `tracking_id` (from `results_nusc.json`) | `track_id` | optional; null for the det file |
| `translation` [x,y,z] | `cx, cy, cz` | already global/ENU |
| `size` [w,l,h] | `length=size[1], width=size[0], height=size[2]` | nuScenes wlh order |
| `rotation` [w,x,y,z] | `qw,qx,qy,qz` via `SE3.from_matrix(pyquaternion(...).rotation_matrix)` or directly | pure rotation, unit norm |
| — | `frame` = `world` | `lidar_nusc_box_to_global` applied ego2global built from the same pose files with the same Euler convention |

Converter sketch (would live in `scripts/griffin_to_predictions.py`, run with coperceive's own venv, not the griffin env):

```python
# API names verified against feat/eval-step-a on 2026-10-17
from coperceive.evaluation.predictions import RunMeta, PredictionBox, write_run, validate_boxes
from coperceive.model import BoxFrame
from coperceive.se3 import SE3
from coperceive.store import read_scene

BASE_US = int((datetime(2025, 1, 1, 9, 0, 0) - datetime(1970, 1, 1)).total_seconds() * 1e6)
sample_json = {s["token"]: s for s in json.load(open(nusc / "sample.json"))}
scene_json = {
    s["token"]: s["name"].split("-", 2)[2] for s in json.load(open(nusc / "scene.json"))
}  # "scene-0000-Town03-000" -> "Town03-000"
rows = []
for token, annos in json.load(open(results_nusc_det))["results"].items():
    ts = sample_json[token]["timestamp"]
    frame_id = round((ts - BASE_US) / 100_000)
    scene_id = scene_json[sample_json[token]["scene_token"]]
    for a in annos:
        w, l, h = a["size"]
        qw, qx, qy, qz = a["rotation"]
        rows.append(
            PredictionBox(
                scene_id=scene_id,
                agent_id="vehicle",
                source="single",
                time_us=frame_id * 100_000,
                category=a["detection_name"],
                score=float(a["detection_score"]),
                track_id=None,
                center=tuple(a["translation"]),
                size_lwh=(l, w, h),
                box_from_rpy=SE3(qw, qx, qy, qz, 0.0, 0.0, 0.0),
                frame=BoxFrame.WORLD,
            )
        )
meta = RunMeta(
    run_id="griffin25m-vehicle-uniad-iter33024",
    model="Griffin UniAD/BEVFormer-tiny R50 vehicle-side iter_33024.pth (MIT, Wang et al. arXiv:2503.06983)",
    score_note="sigmoid class score of the UniAD track head; the model drops boxes below score_thresh=0.4 and a 50 m ego radius",
    categories=("car", "bicycle", "pedestrian"),
    sources=(("single", "vehicle-side camera-only UniAD, no fusion"),),
    created_utc=now_z,
)
for scene_id, group in groupby(rows):
    validate_boxes(read_scene(scenes_root / scene_id), meta, group)
write_run(scenes_root / "predictions", meta.run_id, meta, rows)
```

Evaluation afterwards: `coperceive eval run <run_id> --root <scenes>` with a config that sets `max_range_m: 50` (TODO: verify with Adarsh) so that ground truth beyond the model's 50 m radius cut is ignored rather than counted as misses, and `score_threshold` 0.4 to match the model's own operating point.

## 5. Risks and unknowns

1. **Camera-only models, ~69 GB staging.** The copy to `/local` dominates wall time; the run needs a node with ≥ 100 GB free in `/local` (size of `/local` not established). Alternative: only stage the 10 val scenes (1490 frames × 4 cams ≈ 15 GB) — requires editing `scene_infos.json` or the split file in the staged copy (`# TODO: verify` the converter tolerates missing train scenes).
2. **Ancient stack on Ada GPUs** (torch 1.9.1+cu111 with sm_86 cubins on sm_89; cuDNN 8.0.5). Mitigation: `--constraint=a40|a100`. Driver on those nodes not found.
3. **Build of mmdet3d 0.17.1 ops** with nvcc 11.8 and the node's host gcc; may need `gnu/gcc-7`. Also pip resolution for 2021-era pins on 2026 PyPI (numpy/scipy/setuptools 59.5.0, `pillow==9.0.0`) — `example.yaml` is the fallback source of exact pins.
4. **Conversion step** writes `griffin-nuscenes/` (symlinks + JSON) and `data/infos/*.pkl` on `/local`; run time "~5-15 minutes"; it processes all 47 scenes including train, and the `--early_fusion`/`--delay` flags create extra perspectives we do not need (can be dropped for vehicle-side only: TODO: verify `generate_nuscenes_pkl.py` works without them).
5. **Top-level `vehicle-side.pth` vs `vehicle-side/iter_33024.pth`** differ by 128 bytes; use the one whose md5 matches the docs (`1201f5692e390a75e5b5ab0efa0cae19`), checked inside the job.
6. **Score semantics / operating point.** The track head emits only boxes above `score_thresh=0.4`; AP curves in coperceive will be truncated at 0.4. Lowering it via `--cfg-options model.score_thresh=0.1` is possible but changes the published operating point (question for Adarsh).
7. **Yaw convention.** `output_to_nusc_box` flips yaw (`-yaw - pi/2`); the global quaternion should already be a proper world yaw, but the first run must be checked against GT (`tp_errors.yaw_deg` should be small, not ~90°).
8. `torch.distributed.launch` with one process still needs a free `MASTER_PORT`; set it explicitly.
9. coperceive side: `BoxFrame.WORLD` member name, `read_scene` path layout and whether `write_run` expects the scenes-root `predictions/` dir to exist — all `# TODO: verify` in the sketch.

## 6. Yes/no questions for Adarsh

1. OK to create `~/.conda/envs/griffin` (~6–8 GB, Python 3.8 / torch 1.9.1+cu111) and `~/src/{mmdetection3d,griffin}` via an sbatch install job?
2. OK to run the first inference on A40/A100 nodes (`--constraint=a40`) rather than the PS L40/L40S nodes, to stay on the authors' compiled architectures?
3. Stage all 47 scenes (~69 GB, faithful to the toolkit) or only the 10 val scenes (~15 GB, needs a trimmed `scene_infos.json`)?
4. Use the detection output (`results_nusc_det.json`, `source=single`) for `boxes.parquet`, and leave the tracking output for a later `track_id` column?
5. Keep the model's `score_thresh=0.4` operating point as published, or lower it to get fuller AP curves?
6. Evaluate with `max_range_m: 50` to mirror the toolkit's 50 m radius cut?
7. Is the vehicle-side single-agent run enough for this phase, or should the same job also run drone-side and cooperative (needs drone cameras, +~100 GB staging, and the drone-query extraction step)?
8. May `run.json` `notes` carry the MIT/arXiv attribution text above as-is?
