# Local and cluster

Use a folder on this machine, or a raw tree on a cluster. Paths below are placeholders. Replace `/data/<dataset>` with the directory you have.

## On this machine

The app's **Open Folder** tab checks a local directory (`web/src/desktop/ConnectScreen.tsx`). **Get Data** is the catalog download screen. Griffin is the catalog entry whose status is `ready`. OPV2V, V2XSet, V2V4Real, and DAIR-V2X-C are `local-import`: the catalog notice says nothing is downloaded.

The local viewer is:

```bash
coperceive serve --root /data/<dataset>/scenes
```

## On a cluster

The release README's cluster path is **Servers**: add an SSH profile, then browse, detect, and stream. The commands behind that screen are:

```bash
coperceive dataset detect --path /data/<dataset>
coperceive dataset index --adapter opv2v --raw-root /data/opv2v --split test
coperceive dataset prepare --adapter opv2v --raw-root /data/opv2v --split test \
  --cache /data/opv2v-cache --all
```

`--adapter` accepts `griffin`, `opv2v`, `v2xset`, `v2v4real`, or `dair-v2x-c` (`coperceive dataset prepare --help`). `--split` is described there as the split for OPV2V and V2XSet. V2V4Real and DAIR-V2X-C adapters also require a split; see their pages.

Profiles, partitions, and accounts live in the server profile ([ADR 0015](../adr/0015-cluster-profiles-scheduler-adapter.md)). The app does not ship a site path.

## More detail

- [Open a dataset on a cluster](../guides/cluster.md)
- [Cluster contract](../datasets/cluster-datasets.md)
- [Prepare timing](../datasets/prepare-profile.md)
