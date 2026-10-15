# Griffin checks

Date: 2026-10-15. Dataset bytes stay outside the git tree. This page records what was fetched and what was not.

## Sources

- arXiv abs page `https://arxiv.org/abs/2503.06983`, fetched 2026-10-15.
- Toolkit `https://github.com/wang-jh18-SVM/Griffin`: `README.md`, `docs/Dataset_Preparation.md`, `LICENSE.txt`.
- Hugging Face dataset `wjh-svm/Griffin`: README YAML and the tree API under `datasets/`.
- Local files, MD5-checked, downloaded earlier with `huggingface_hub` into `/tmp/griffin-25m` (not in git): `md5.txt`, `drone_metadata.zip`, `vehicle_metadata.zip`, `vehicle_lidar.zip` for `griffin_50scenes_25m`.

The downloader in `coperceive.griffin.download` allow-lists those three zips plus `md5.txt`. It does not log tokens.

## Paper and toolkit

The abs page title is “Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark”. Visible authors, in order: Jiahao Wang, Xiangyu Cao, Jiaru Zhong, Yuner Zhang, Zeyu Han, Haibao Yu, Chuang Zhang, Lei He, Shaobing Xu, Jianqiang Wang. The same page’s download note says “by Jiahao Wang and 8 other authors”, which is one short of that list. `citation_doi` on the page is `10.1609/aaai.v40i12.37951`. The proceedings PDF was not opened.

The toolkit README bibtex lists seven authors and omits Zeyu Han, Chuang Zhang, and Jianqiang Wang. Cite the abs-page list.

The abstract says “over 250 dynamic scenes (37k+ frames)” and altitudes 20–60 m. `Dataset_Preparation.md` says “three subsets” and then lists four:

| Subset in the doc | Band | Scenes in the doc |
| --- | --- | --- |
| Griffin-Random | 20–60 m | 104 |
| Griffin-25m | 25 ± 2 m | 47 |
| Griffin-40m | 40 ± 2 m | 54 |
| Griffin-55m | 55 ± 2 m | 50 |

47 + 54 + 50 + 104 = 255. The doc also says each scene lasts about 15 seconds and about 150 frames, and that the four subsets total over 37.7k samples, 339.3k images, and 914.8k 3D annotations. Those totals were not recomputed. Only the 25 m metadata and vehicle LiDAR were opened.

`LICENSE.txt` is the MIT licence, copyright line `Copyright (c) [2025] [Jiahao Wang]`. The Hugging Face README YAML says `license: mit`. The dataset card body says “over 200 dynamic scenes” and “more than 30,000 frames and 270,000 images”. That is looser and smaller than the paper abstract and the toolkit totals.

## Hugging Face files

Tree API, 2026-10-15. Sizes are the API `size` field, in bytes.

`griffin_50scenes_25m` has 15 files and sums to 167,190,016,122 bytes. The three files used here:

| File | Bytes | MD5 in `md5.txt` | MD5 of the local file |
| --- | --- | --- | --- |
| drone_metadata.zip | 14,384,997 | `48e23147f1acf0761fb42340f677ad8c` | same |
| vehicle_metadata.zip | 10,876,283 | `e119733a51a0ee8125017f986d26833d` | same |
| vehicle_lidar.zip | 214,487,013 | `f044c8f1671bdfe4da21fc405abe8da7` | same |

Together those three are 239,748,293 bytes. `md5.txt` itself is 898 bytes. Its self-line lists `904ee8d88c0d1db9878337298697306a`. The computed digest of the file is `1cbc937bd675d2bee7c42919415e3782`. The downloader ignores that self-line and checks the payload names it actually fetched. `md5sum -c md5.txt` therefore fails on `md5.txt` and on every camera zip that was not downloaded.

`griffin_50scenes_40m` and `griffin_50scenes_55m` each have the same 15 zip names and no `.z01`. `griffin_100scenes_random` has 24 files. Its large camera RGB archives are split: each `*.z01` is 26,843,545,600 bytes (25 GiB) plus a `*.zip`. Instance segmentation, metadata, and LiDAR on the random subset are single zips.

The four `datasets/` folders sum to 967,266,439,129 bytes. The card’s “973 GB” was not recomputed from `ckpts/` plus these folders. `ckpts/` was not listed.

## What the 25 m metadata contains

Both `scene_infos.json` files are equal. They contain 47 scenes. The folder name says 50. Map strings: Town03 13, Town06 12, Town07 10, Town10HD 12.

Frame counts: 45 scenes with 149 frames, one with 148, one with 147. Sum 7,000. Not 150. At the paper’s 10 Hz that is 14.7–14.9 s. `Town03-000` is frames 11185 through 11333, 149 frames.

`cruise_altitude` over the 47 scenes: min 23.084 m, median 25.236 m, max 26.919 m. That sits inside 25 ± 2 m. It is a per-scene cruise value, not a per-frame drone-minus-vehicle height. Per-frame vertical separation was measured in an earlier pass and was not repeated here.

`map_name` is a string such as `Carla/Maps/Town03`. The extracted tree has no map geometry. The claim of a 0.24 m median distance from road centrelines was not measured. There is no centreline in the downloaded archives.

Vehicle and drone pose files use the same frame ids. On `Town03-000` frame 11185, the pose JSON field `timestamp` is the integer 11185, equal to the file stem. Keys present: `pitch`, `roll`, `timestamp`, `velocity`, `x`, `y`, `yaw`, `z`.

## Pose convention, remeasured

On `Town03-000`, 15 frames (every 10th), 251 shared track ids: with `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)` in degrees, the world points of a shared id from the vehicle box and the drone box differ by at most 5.7e-14 m. Category mismatches in that sample: 0.

So both agents are in one right-handed world frame for those boxes. The paper’s converted world is described as ENU. This check did not compare a zero-yaw axis to a map, so it does not decide east-versus-north. It does show the two agents are not in two unrelated frames. The scene model stores both in one scene.

The reader sets `time_us = frame_id * 100_000` and writes that assumption into the clock note. The files do not contain a measured wall clock. 100 ms is the 10 Hz lidar rate from the paper, not a timestamp recovered from the JSON.

## LiDAR files

`vehicle_lidar.zip` contains 7,000 `.ply` members and 0 `.bin` members. A sample member starts:

```
ply
format ascii 1.0
element vertex 2755
property float32 x
property float32 y
property float32 z
property float32 I
```

`Dataset_Preparation.md` shows `lidar/lidar_top` and, elsewhere in the toolkit notes used during the spike, treats lidar as `.bin`. The bytes in this zip are ASCII PLY. Camera image bytes were not downloaded, so image resolution was not remeasured from pixels. Calibration JSON is in the metadata zip. The viewer spike read intrinsics from those files for frustums and did not decode images.

No drone lidar zip is in the 25 m file list. The drone calib directory can still name lidar stems. The paper says the drone has no lidar payload. Those calib files were not treated as a point-cloud sensor in the format spike.

## Not verified

- Scene counts inside the 40 m, 55 m, and random metadata. Only the toolkit’s stated counts are quoted.
- The NuScenes conversion, including any `maps/` directory it might write.
- Road centrelines.
- A wall-clock check of the 10 Hz assumption.
- Which horizontal axis is east.
