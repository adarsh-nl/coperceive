# CARLA town maps (OPV2V / V2XSet)

OPV2V and V2XSet record CARLA towns but **the scene files do not name the town**
([OpenCOOD data intro](https://github.com/DerrickXuNu/OpenCOOD/blob/main/docs/md_files/data_intro.md),
ADR 0012 decision 6). Coperceive identifies the town from the vehicle poses with
`coperceive maps identify`, never from a guessed filename. If no town wins the
ADR 0008 rule, the scene shows **no map** and the reason (for example
`ambiguous: Town06 0.046 m vs Town03 0.245 m`). Culver City is a digital twin,
not a stock CARLA town, so it has no downloadable map.

## Licence

CARLA-specific **code** is MIT. CARLA-specific **assets**, including the town
OpenDRIVE files, are **CC-BY**; the CARLA 0.9.15 README does not state a CC-BY
version, so Coperceive says "CC-BY", not "CC-BY 4.0":

- https://github.com/carla-simulator/carla/blob/0.9.15/README.md
- https://github.com/carla-simulator/carla/blob/0.9.15/LICENSE

The files are not committed or bundled. The app downloads one `.xodr` on demand,
from the pinned mirror below, only after the SHA-256 matches the hash measured
from the CARLA 0.9.15 package in **ADR 0008**. No CARLA bytes are in this
repository.

## Verified one-click town table

Source: `hihuaweizhu/scenic-carla`, pinned commit
`13d68ba8e9ea7c45f13250c37f6c2350829146f6`, path `maps/CARLA/<Town>.xodr`.
Every SHA-256 below was downloaded and re-checked against ADR 0008 on 2026-11-05.

| Town | SHA-256 (ADR 0008) | Bytes | Source | Licence |
| --- | --- | ---: | --- | --- |
| Town01 | `97a7f6ac…b709` | 973,108 | pinned mirror | CC-BY |
| Town02 | `953c05f1…dd7d` | 915,949 | pinned mirror | CC-BY |
| Town03 | `abf177bc…13c9` | 4,589,872 | pinned mirror | CC-BY |
| Town04 | `4dddd156…6c85` | 3,403,393 | pinned mirror | CC-BY |
| Town05 | `7edc53b0…48851` | 4,651,162 | pinned mirror | CC-BY |
| Town06 | `a90b6131…496e` | 3,004,885 | pinned mirror | CC-BY |
| Town07 | `be730652…77bf` | 2,229,151 | pinned mirror | CC-BY |
| Town10HD | `5d883b79…7be7` | 1,857,682 | pinned mirror | CC-BY |

The full hashes are in `coperceive/hdmap/install.py` and ADR 0008. Town03 used
the original one-click installer; the others use the same verified path. If the
mirror is unreachable or the bytes differ, the app refuses and tells the user to
use `coperceive maps fetch` with their own CARLA 0.9.15 package.

## Cache and identification

Maps live at `<library_root>/maps/<Town>/` (`manifest.json`, `polylines.parquet`,
`vertices.bin`), never inside a dataset. Settings → Maps lists each town with
size, source, licence and verified state, and offers Install/Remove.

After installing a town, Coperceive scores the library scenes' vehicle poses
against every installed map at identity (ADR 0008/0012). A town is accepted only
if its median lane-centre residual is ≤ 0.5 m, the runner-up is ≥ 10× worse and
≥ 0.5 m worse; that result is written into the scene's `map_name` platform field
and the map is aligned. Unidentified scenes keep the reason and no map.

The viewer uses the existing road-map layer; no new renderer. The OpenDRIVE
parser applies the same left-handed → schema reflection as OPV2V/V2XSet
(ADR 0012), and the map/pose alignment is measured rather than assumed.

**Cluster scenes:** maps are downloaded and cached on the Mac; the viewer draws
them locally. Nothing is written on the cluster.
