# coperceive

Cooperative multi-agent perception data. The project is [coperceive](https://github.com/adarsh-nl/coperceive). The name is recorded in [naming.md](design/naming.md). The licence is Apache-2.0 and the repository stays private.

Phase 1 is the scene model and the hybrid on-disk layout. The contract is [format.md](spec/format.md). Phase 2 downloads Griffin and converts one release into that layout. Phase 3 is `Scene.at`, camera projection, cross-agent box queries, and `CooperativeDataset` in the torch extra. Phase 4 is `coperceive validate`, which writes a JSON report and a Markdown report. Phase 5 draws a scene with three.js inside the local page and can write a Rerun `.rrd`. A shared HD map is a sidecar keyed by `map_name` ([ADR 0008](adr/0008-hd-map.md)). There is no second dataset and no desktop shell.

- [Positioning](design/positioning.md)
- [Name](design/naming.md)
- [Earlier candidates](design/names.md)
- [Griffin checks](design/griffin-verification.md)
- [Griffin dataset](datasets/griffin.md)
- [Validate](validate.md)
- [Viewer](viewer.md)
- [Decisions](adr/0001-data-model.md)
