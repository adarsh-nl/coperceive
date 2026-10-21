// SPDX-License-Identifier: Apache-2.0
//! Persistent application state management for Coperceive Desktop.
//!
//! Stores session resume parameters (last dataset, scene, sample, viewpoint, theme)
//! and recent dataset roots in `~/Library/Application Support/nl.adarsh.coperceive/state.json`
//! with strict permissions (directory 0700, file 0600).

use std::fs;
use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Maximum number of recent dataset roots remembered.
pub const MAX_RECENT_ROOTS: usize = 5;

/// Persistent desktop application state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppPersistentState {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_data_root: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_scene_id: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_sample_index: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_timestamp_ns: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_viewpoint: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_theme: Option<String>,

    #[serde(default)]
    pub recent_roots: Vec<String>,
}

impl Default for AppPersistentState {
    fn default() -> Self {
        Self {
            last_data_root: None,
            last_scene_id: None,
            last_sample_index: None,
            last_timestamp_ns: None,
            last_viewpoint: Some("orbit".into()),
            last_theme: Some("dark".into()),
            recent_roots: Vec::new(),
        }
    }
}

/// Resolves the default persistent state file path for Coperceive desktop.
///
/// macOS: `~/Library/Application Support/nl.adarsh.coperceive/state.json`
/// Other Unix: `$XDG_DATA_HOME/nl.adarsh.coperceive/state.json` or `~/.local/share/nl.adarsh.coperceive/state.json`
pub fn get_state_file_path() -> PathBuf {
    if let Some(home) = std::env::var_os("HOME") {
        #[cfg(target_os = "macos")]
        {
            return PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("nl.adarsh.coperceive")
                .join("state.json");
        }
        #[cfg(not(target_os = "macos"))]
        {
            if let Some(xdg) = std::env::var_os("XDG_DATA_HOME") {
                return PathBuf::from(xdg)
                    .join("nl.adarsh.coperceive")
                    .join("state.json");
            }
            return PathBuf::from(home)
                .join(".local")
                .join("share")
                .join("nl.adarsh.coperceive")
                .join("state.json");
        }
    }
    PathBuf::from(".coperceive-state.json")
}

/// Adds a dataset root path to the front of `recent_roots`, deduplicating and
/// capping to `MAX_RECENT_ROOTS`.
pub fn add_recent_root(state: &mut AppPersistentState, root: &str) {
    let trimmed = root.trim();
    if trimmed.is_empty() {
        return;
    }
    state.recent_roots.retain(|r| r != trimmed);
    state.recent_roots.insert(0, trimmed.to_string());
    if state.recent_roots.len() > MAX_RECENT_ROOTS {
        state.recent_roots.truncate(MAX_RECENT_ROOTS);
    }
}

/// Validates whether a directory is a valid Coperceive dataset root.
///
/// A valid dataset root is an existing directory that either:
/// 1. Directly contains `manifest.json` (single scene root), or
/// 2. Contains at least one direct subdirectory containing `manifest.json` (multi-scene root).
pub fn validate_dataset_root(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!("Dataset directory does not exist: {}", path.display()));
    }
    if !path.is_dir() {
        return Err(format!("Path is not a directory: {}", path.display()));
    }

    if path.join("manifest.json").is_file() {
        return Ok(());
    }

    let entries = fs::read_dir(path).map_err(|e| format!("Cannot read directory: {}", e))?;
    for entry in entries.flatten() {
        let child_path = entry.path();
        if child_path.is_dir() && child_path.join("manifest.json").is_file() {
            return Ok(());
        }
    }

    Err("Not a valid coperceive dataset: missing manifest.json".into())
}

/// Loads persistent application state from the default state path.
///
/// If the file does not exist or contains invalid/corrupt JSON, falls back to
/// `AppPersistentState::default()` safely without crashing.
pub fn load_state() -> AppPersistentState {
    let path = get_state_file_path();
    load_state_from_path(&path)
}

/// Loads persistent application state from a specific path.
pub fn load_state_from_path(path: &Path) -> AppPersistentState {
    if !path.exists() {
        return AppPersistentState::default();
    }

    let contents = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[state] Warning: failed to read state file {}: {}", path.display(), e);
            return AppPersistentState::default();
        }
    };

    match serde_json::from_str::<AppPersistentState>(&contents) {
        Ok(s) => s,
        Err(e) => {
            eprintln!(
                "[state] Warning: corrupt state file {}: {}. Falling back to default state.",
                path.display(),
                e
            );
            AppPersistentState::default()
        }
    }
}

/// Saves persistent state to the default state path.
///
/// Ensures the parent directory has 0700 permissions and the state file has 0600 permissions.
pub fn save_state(state: &AppPersistentState) -> Result<(), String> {
    let path = get_state_file_path();
    save_state_to_path(state, &path)
}

/// Saves persistent state to a specified path with strict permissions (dir 0700, file 0600).
pub fn save_state_to_path(state: &AppPersistentState, path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.exists() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create state directory {}: {}", parent.display(), e))?;

            #[cfg(unix)]
            {
                let perms = fs::Permissions::from_mode(0o700);
                let _ = fs::set_permissions(parent, perms);
            }
        }
    }

    let serialized = serde_json::to_string_pretty(state)
        .map_err(|e| format!("Failed to serialize application state: {}", e))?;

    #[cfg(unix)]
    {
        use std::io::Write;
        let mut file = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .mode(0o600)
            .open(path)
            .map_err(|e| format!("Failed to open state file {}: {}", path.display(), e))?;

        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));

        file.write_all(serialized.as_bytes())
            .map_err(|e| format!("Failed to write state file: {}", e))?;
    }

    #[cfg(not(unix))]
    {
        fs::write(path, serialized)
            .map_err(|e| format!("Failed to write state file {}: {}", path.display(), e))?;
    }

    Ok(())
}

#[cfg(test)]
pub mod tests {
    use super::*;

    #[test]
    fn test_state_serialize_deserialize() {
        let state = AppPersistentState {
            last_data_root: Some("/path/to/data".into()),
            last_scene_id: Some("Town03-000".into()),
            last_sample_index: Some(42),
            last_timestamp_ns: Some(4200000000),
            last_viewpoint: Some("chase".into()),
            last_theme: Some("light".into()),
            recent_roots: vec!["/path/to/data".into(), "/path/to/prev".into()],
        };

        let json = serde_json::to_string(&state).expect("serialize");
        let deserialized: AppPersistentState = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(state, deserialized);
    }

    #[test]
    fn test_corrupt_state_fallback() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-state-corrupt-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);
        let state_path = tmp.join("state.json");

        fs::write(&state_path, "{ broken json ... ").expect("write corrupt");

        let loaded = load_state_from_path(&state_path);
        assert_eq!(loaded, AppPersistentState::default());
        assert_eq!(loaded.last_viewpoint, Some("orbit".into()));
        assert_eq!(loaded.last_theme, Some("dark".into()));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_recent_roots_dedup_and_max_5() {
        let mut state = AppPersistentState::default();
        add_recent_root(&mut state, "/data/1");
        add_recent_root(&mut state, "/data/2");
        add_recent_root(&mut state, "/data/3");
        add_recent_root(&mut state, "/data/4");
        add_recent_root(&mut state, "/data/5");
        add_recent_root(&mut state, "/data/6");

        assert_eq!(state.recent_roots.len(), 5);
        assert_eq!(state.recent_roots[0], "/data/6");
        assert_eq!(state.recent_roots[4], "/data/2");

        // Re-adding existing moves it to the front without duplicating
        add_recent_root(&mut state, "/data/3");
        assert_eq!(state.recent_roots.len(), 5);
        assert_eq!(state.recent_roots[0], "/data/3");
        assert_eq!(state.recent_roots[1], "/data/6");
    }

    #[test]
    #[cfg(unix)]
    fn test_permissions_dir_0700_file_0600() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-perms-{}", std::process::id()));
        let sub = tmp.join("app_support_dir");
        let state_path = sub.join("state.json");

        let mut state = AppPersistentState::default();
        state.last_data_root = Some("/tmp/synth".into());

        save_state_to_path(&state, &state_path).expect("save state");

        let dir_meta = fs::metadata(&sub).expect("dir meta");
        let dir_mode = dir_meta.permissions().mode() & 0o777;
        assert_eq!(dir_mode, 0o700, "Directory mode should be 0700");

        let file_meta = fs::metadata(&state_path).expect("file meta");
        let file_mode = file_meta.permissions().mode() & 0o777;
        assert_eq!(file_mode, 0o600, "State file mode should be 0600");

        let loaded = load_state_from_path(&state_path);
        assert_eq!(loaded.last_data_root, Some("/tmp/synth".into()));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_validate_dataset_root() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-validate-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);

        // Non-existent path
        assert!(validate_dataset_root(&tmp.join("nonexistent")).is_err());

        // Empty directory: invalid (missing manifest.json)
        let empty_dir = tmp.join("empty");
        let _ = fs::create_dir_all(&empty_dir);
        let err = validate_dataset_root(&empty_dir).unwrap_err();
        assert!(err.contains("missing manifest.json"));

        // Single scene directory with manifest.json: valid
        let single_scene = tmp.join("single_scene");
        let _ = fs::create_dir_all(&single_scene);
        fs::write(single_scene.join("manifest.json"), "{}").expect("write manifest");
        assert!(validate_dataset_root(&single_scene).is_ok());

        // Multi-scene root with child containing manifest.json: valid
        let multi_root = tmp.join("multi_root");
        let child_scene = multi_root.join("scene_001");
        let _ = fs::create_dir_all(&child_scene);
        fs::write(child_scene.join("manifest.json"), "{}").expect("write manifest");
        assert!(validate_dataset_root(&multi_root).is_ok());

        let _ = fs::remove_dir_all(&tmp);
    }
}
