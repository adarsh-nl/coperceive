# Open a dataset on a cluster

Point Coperceive at a raw tree that is already on the cluster. The app does not contain a site path. Use `/data/<dataset>` as the stand-in below.

## Steps

1. In the app, open **Servers** and add an SSH profile (release README). Partitions and accounts come from that profile ([ADR 0015](../adr/0015-cluster-profiles-scheduler-adapter.md)).
2. Detect the tree:

    ```bash
    coperceive dataset detect --path /data/opv2v
    ```

3. Index one split, then prepare scenes into a cache. `--adapter` is `griffin`, `opv2v`, `v2xset`, `v2v4real`, or `dair-v2x-c`:

    ```bash
    coperceive dataset index --adapter opv2v --raw-root /data/opv2v --split test
    coperceive dataset prepare --adapter opv2v --raw-root /data/opv2v --split test \
      --cache /data/opv2v-cache --all
    ```

`--split` help text names OPV2V and V2XSet. Pass the split the adapter allows: `train` or `test` for V2V4Real, `train` or `val` for DAIR-V2X-C. Griffin's manifest has subsets, not those splits. `coperceive dataset prepare --help` says `--split` with an adapter that has no splits is not described there; the cluster contract page says a `--split` on Griffin is refused.

4. Stream a prepared cache with `coperceive dataset stream --root /data/opv2v-cache` over the SSH session the app opens. The help text calls it a read-only file streamer.

## More detail

- [Cluster contract](../datasets/cluster-datasets.md)
- [Prepare timing](../datasets/prepare-profile.md)
- [Local and cluster](../get-started/local-and-cluster.md)
