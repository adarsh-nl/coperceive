// SPDX-License-Identifier: Apache-2.0
//! The viewer's cluster dataset metadata while a dataset is streamed over SSH.
//!
//! The app's viewer-job path (`RemoteSessionManager`, `RemoteClient`) was removed:
//! viewing a cluster dataset runs no Slurm job (`dataset_open`). The `coperceive
//! remote` CLI on the cluster is separate and unchanged.

/// Clears active cluster dataset metadata unless a cluster dataset is being
/// streamed (`has_remote`), so stale streaming headers cannot linger after
/// switching to a local scene.
pub fn resolve_viewer_dataset_scenes(
    has_remote: bool,
    dataset: serde_json::Value,
) -> serde_json::Value {
    if has_remote {
        dataset
    } else {
        serde_json::Value::Null
    }
}

#[cfg(test)]
mod scene_tests {
    use super::resolve_viewer_dataset_scenes;

    #[test]
    fn dataset_scenes_is_cleared_when_no_remote_session_is_active() {
        let cluster_dataset = serde_json::json!({
            "scenes": [{ "scene_id": "train_0000", "state": "ready" }],
            "about": { "label": "V2XSET train", "server": "HPC" }
        });
        assert_eq!(
            resolve_viewer_dataset_scenes(false, cluster_dataset.clone()),
            serde_json::Value::Null
        );
        assert_eq!(
            resolve_viewer_dataset_scenes(true, cluster_dataset.clone()),
            cluster_dataset
        );
    }
}
