// SPDX-License-Identifier: Apache-2.0
//! Persistent application state management for Coperceive Desktop.
//!
//! Stores session resume parameters (last dataset, scene, sample, viewpoint, theme)
//! and recent dataset roots in `~/Library/Application Support/nl.adarsh.coperceive/state.json`
//! with strict permissions (directory 0700, file 0600).

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Maximum number of recent dataset roots remembered.
pub const MAX_RECENT_ROOTS: usize = 5;

fn default_true() -> bool {
    true
}

/// Window dimensions and positioning on screen.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WindowState {
    pub width: f64,
    pub height: f64,
    pub x: f64,
    pub y: f64,
    #[serde(default)]
    pub is_maximized: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub monitor: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scale_factor: Option<f64>,
}

/// Monitor descriptor representing a display's visible work frame in logical points.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MonitorInfo {
    pub name: Option<String>,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale_factor: f64,
    pub is_primary: bool,
}

impl WindowState {
    /// Constructs a WindowState from physical pixel position and size and scale factor.
    pub fn from_physical(
        physical_x: i32,
        physical_y: i32,
        physical_width: u32,
        physical_height: u32,
        scale_factor: f64,
        monitor: Option<String>,
        is_maximized: bool,
    ) -> Self {
        let scale = if scale_factor > 0.0 { scale_factor } else { 1.0 };
        Self {
            x: physical_x as f64 / scale,
            y: physical_y as f64 / scale,
            width: physical_width as f64 / scale,
            height: physical_height as f64 / scale,
            is_maximized,
            monitor,
            scale_factor: Some(scale),
        }
    }

    /// Converts logical coordinates to physical pixels given a scale factor.
    pub fn to_physical(&self, scale_factor: f64) -> (i32, i32, u32, u32) {
        let scale = if scale_factor > 0.0 { scale_factor } else { 1.0 };
        let px = (self.x * scale).round() as i32;
        let py = (self.y * scale).round() as i32;
        let pw = (self.width * scale).round().max(1.0) as u32;
        let ph = (self.height * scale).round().max(1.0) as u32;
        (px, py, pw, ph)
    }
}

/// Clamps and validates the window state against available monitors.
/// If the saved monitor is missing, or the rect would end up mostly off-screen (<50% overlap),
/// centres the window on the primary monitor.
/// Clamps the window fully inside the visible frame of the resolved monitor.
pub fn resolve_window_geometry(
    saved: &WindowState,
    monitors: &[MonitorInfo],
) -> WindowState {
    if monitors.is_empty() {
        return saved.clone();
    }

    let primary = monitors
        .iter()
        .find(|m| m.is_primary)
        .or_else(|| monitors.first())
        .expect("monitors not empty");

    let candidate = if let Some(saved_name) = &saved.monitor {
        monitors.iter().find(|m| m.name.as_deref() == Some(saved_name.as_str()))
    } else {
        // Fallback for legacy state: find monitor with >= 50% overlap
        monitors.iter().find(|m| {
            let w = if saved.width > 0.0 { saved.width } else { 1400.0 };
            let h = if saved.height > 0.0 { saved.height } else { 900.0 };
            let win_area = w * h;
            let overlap_w = (f64::min(saved.x + w, m.x + m.width) - f64::max(saved.x, m.x)).max(0.0);
            let overlap_h = (f64::min(saved.y + h, m.y + m.height) - f64::max(saved.y, m.y)).max(0.0);
            let overlap_area = overlap_w * overlap_h;
            win_area > 0.0 && overlap_area >= 0.5 * win_area
        })
    };

    let (target_mon, should_center) = match candidate {
        Some(mon) => {
            let w = if saved.width > 0.0 { saved.width } else { 1400.0 };
            let h = if saved.height > 0.0 { saved.height } else { 900.0 };
            let win_area = w * h;

            let overlap_w = (f64::min(saved.x + w, mon.x + mon.width) - f64::max(saved.x, mon.x)).max(0.0);
            let overlap_h = (f64::min(saved.y + h, mon.y + mon.height) - f64::max(saved.y, mon.y)).max(0.0);
            let overlap_area = overlap_w * overlap_h;

            if win_area <= 0.0 || overlap_area < 0.5 * win_area {
                // Mostly off-screen: centre on primary
                (primary, true)
            } else {
                (mon, false)
            }
        }
        None => {
            // Monitor gone or no match: centre on primary
            (primary, true)
        }
    };

    let mut w = if saved.width > 0.0 { saved.width } else { 1400.0 };
    let mut h = if saved.height > 0.0 { saved.height } else { 900.0 };

    if w > target_mon.width {
        w = target_mon.width;
    }
    if h > target_mon.height {
        h = target_mon.height;
    }

    let (x, y) = if should_center {
        let cx = target_mon.x + (target_mon.width - w) / 2.0;
        let cy = target_mon.y + (target_mon.height - h) / 2.0;
        (cx, cy)
    } else {
        let mut cx = saved.x;
        let mut cy = saved.y;

        if cx < target_mon.x {
            cx = target_mon.x;
        } else if cx + w > target_mon.x + target_mon.width {
            cx = target_mon.x + target_mon.width - w;
        }

        if cy < target_mon.y {
            cy = target_mon.y;
        } else if cy + h > target_mon.y + target_mon.height {
            cy = target_mon.y + target_mon.height - h;
        }

        (cx, cy)
    };

    WindowState {
        width: w,
        height: h,
        x,
        y,
        is_maximized: saved.is_maximized,
        monitor: target_mon.name.clone(),
        scale_factor: Some(target_mon.scale_factor),
    }
}

fn default_frustum_opacity() -> f64 {
    0.85
}

/// Toggles for sensor, prediction, map, and reference layers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LayerToggles {
    #[serde(default = "default_true")]
    pub points: bool,
    #[serde(default = "default_true")]
    pub boxes: bool,
    #[serde(default = "default_true")]
    pub frustums: bool,
    #[serde(default = "default_true")]
    pub map: bool,
    #[serde(default = "default_true")]
    pub axes: bool,
    #[serde(default)]
    pub frustum_images: bool,
    #[serde(default = "default_frustum_opacity")]
    pub frustum_opacity: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frustum_image_cameras: Option<Vec<String>>,
}

impl Default for LayerToggles {
    fn default() -> Self {
        Self {
            points: true,
            boxes: true,
            frustums: true,
            map: true,
            axes: true,
            frustum_images: false,
            frustum_opacity: 0.85,
            frustum_image_cameras: None,
        }
    }
}

/// Side panel and camera strip collapse state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PanelCollapseState {
    #[serde(default)]
    pub left_collapsed: bool,
    #[serde(default)]
    pub right_collapsed: bool,
    #[serde(default = "default_true")]
    pub camera_strip_open: bool,
}

impl Default for PanelCollapseState {
    fn default() -> Self {
        Self {
            left_collapsed: false,
            right_collapsed: false,
            camera_strip_open: true,
        }
    }
}

pub const DEFAULT_LEFT_PX: f64 = 248.0;
pub const DEFAULT_RIGHT_PX: f64 = 268.0;
pub const DEFAULT_STRIP_PX: f64 = 96.0;

pub const MIN_LEFT_PX: f64 = 180.0;
pub const MAX_LEFT_PX: f64 = 480.0;

pub const MIN_RIGHT_PX: f64 = 200.0;
pub const MAX_RIGHT_PX: f64 = 480.0;

pub const MIN_STRIP_PX: f64 = 64.0;
pub const MAX_STRIP_PX: f64 = 240.0;

fn default_left_px() -> f64 {
    DEFAULT_LEFT_PX
}

fn default_right_px() -> f64 {
    DEFAULT_RIGHT_PX
}

fn default_strip_px() -> f64 {
    DEFAULT_STRIP_PX
}

/// Panel and camera strip layout sizes in pixels.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LayoutSizes {
    #[serde(default = "default_left_px")]
    pub left_px: f64,
    #[serde(default = "default_right_px")]
    pub right_px: f64,
    #[serde(default = "default_strip_px")]
    pub strip_px: f64,
}

impl Default for LayoutSizes {
    fn default() -> Self {
        Self {
            left_px: DEFAULT_LEFT_PX,
            right_px: DEFAULT_RIGHT_PX,
            strip_px: DEFAULT_STRIP_PX,
        }
    }
}

impl LayoutSizes {
    pub fn clamp(&mut self) {
        self.left_px = self.left_px.clamp(MIN_LEFT_PX, MAX_LEFT_PX);
        self.right_px = self.right_px.clamp(MIN_RIGHT_PX, MAX_RIGHT_PX);
        self.strip_px = self.strip_px.clamp(MIN_STRIP_PX, MAX_STRIP_PX);
    }
}

/// A Griffin download that stopped before it finished.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PendingDownload {
    pub dataset_id: String,
    pub preset_id: String,
}

/// One cached `coperceive validate` result for a scene directory.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ValidationRecord {
    /// True when the report read one scene, failed none, and had no hard failures.
    pub valid: bool,
    /// `manifest.json` modification time in milliseconds since the epoch when checked.
    pub manifest_mtime_ms: u64,
}

/// Persistent desktop application state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AppPersistentState {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_data_root: Option<String>,

    /// Dedicated storage location for imported and downloaded datasets (default ~/coperceive-data).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub library_root: Option<String>,

    /// Background `coperceive validate` results for library scenes, keyed by scene directory.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validation_cache: Option<HashMap<String, ValidationRecord>>,

    /// Download updates in the background. Default on.
    #[serde(default = "default_true")]
    pub auto_download_updates: bool,

    /// Set while a catalog download is unfinished so the next launch can resume it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pending_download: Option<PendingDownload>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_scene_id: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_sample_index: Option<i64>,

    /// Timestamp of current frame in microseconds (µs).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_timestamp_us: Option<i64>,

    /// Legacy field for backwards compatibility with earlier state versions.
    #[serde(default, skip_serializing_if = "Option::is_none", rename = "last_timestamp_ns")]
    pub _legacy_timestamp_ns: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_viewpoint: Option<String>,

    /// User's loop choice. Absent until they toggle it; the viewer then
    /// defaults loop on for scenes shorter than 60 s.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_loop: Option<bool>,

    /// Explicit user theme override ("light" or "dark"). None means follow system appearance.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub theme_override: Option<String>,

    /// Legacy field from older versions: `last_theme: "dark"`.
    /// Deserialized for backwards compatibility, but ignored and never sets `theme_override`.
    #[serde(default, skip_serializing, rename = "last_theme")]
    pub _legacy_last_theme: Option<String>,

    /// Reopen last session on launch (default true).
    #[serde(default = "default_true")]
    pub reopen_last_session: bool,

    /// Agent visibility mapping (agent_id -> is_visible).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_visibility: Option<HashMap<String, bool>>,

    /// Layer visibility toggles.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layer_toggles: Option<LayerToggles>,

    /// Selected camera sensor ID (e.g. "vehicle1/cam_front").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selected_camera: Option<String>,

    /// Split view enabled state.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub split_view: Option<bool>,

    /// Panel collapse state.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub panel_collapse: Option<PanelCollapseState>,

    /// Resizable panel and camera strip layout sizes in pixels.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layout: Option<LayoutSizes>,

    /// Window geometry and state.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub window_state: Option<WindowState>,

    #[serde(default)]
    pub recent_roots: Vec<String>,
}

/// Returns the default library location: ~/coperceive-data.
pub fn default_library_root() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home).join("coperceive-data")
    } else {
        PathBuf::from("coperceive-data")
    }
}

impl Default for AppPersistentState {
    fn default() -> Self {
        Self {
            last_data_root: None,
            library_root: None,
            validation_cache: None,
            auto_download_updates: true,
            pending_download: None,
            last_scene_id: None,
            last_sample_index: None,
            last_timestamp_us: None,
            _legacy_timestamp_ns: None,
            last_viewpoint: Some("orbit".into()),
            last_loop: None,
            theme_override: None,
            _legacy_last_theme: None,
            reopen_last_session: true,
            agent_visibility: None,
            layer_toggles: Some(LayerToggles::default()),
            selected_camera: None,
            split_view: None,
            panel_collapse: Some(PanelCollapseState::default()),
            layout: None,
            window_state: None,
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

fn name_ends_with_partial(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.ends_with(".partial"))
}

/// Validates whether a directory is a valid Coperceive dataset root.
///
/// A valid dataset root is an existing directory that either:
/// 1. Directly contains `manifest.json` and `COMPLETE.json` (single scene root), or
/// 2. Contains at least one direct subdirectory containing `manifest.json` and `COMPLETE.json` (multi-scene root).
/// A restored scene id is usable only when it is a plain name and that scene is
/// still present under the current library/data root.
pub fn restored_scene_exists(root: &Path, scene_id: &str) -> bool {
    let trimmed = scene_id.trim();
    if trimmed.is_empty()
        || trimmed == "."
        || trimmed == ".."
        || trimmed.contains('/')
        || trimmed.contains('\\')
        || trimmed.starts_with('.')
    {
        return false;
    }
    root.join(trimmed).is_dir()
}

pub fn validate_dataset_root(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!("Dataset directory does not exist: {}", path.display()));
    }
    if !path.is_dir() {
        return Err(format!("Path is not a directory: {}", path.display()));
    }

    if !name_ends_with_partial(path)
        && path.join("manifest.json").is_file()
        && path.join("COMPLETE.json").is_file()
    {
        return Ok(());
    }

    let entries = fs::read_dir(path).map_err(|e| format!("Cannot read directory: {}", e))?;
    for entry in entries.flatten() {
        let child_path = entry.path();
        if child_path.is_dir()
            && !name_ends_with_partial(&child_path)
            && child_path.join("manifest.json").is_file()
            && child_path.join("COMPLETE.json").is_file()
        {
            return Ok(());
        }
    }

    Err(format!(
        "No coperceive scenes in {}. A scene folder contains manifest.json and COMPLETE.json.",
        path.display()
    ))
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
        Ok(mut s) => {
            if s.last_timestamp_us.is_none() && s._legacy_timestamp_ns.is_some() {
                s.last_timestamp_us = s._legacy_timestamp_ns.map(|ns| ns / 1000);
            }
            s
        }
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
    fn restored_scene_must_still_be_under_the_library_root() {
        let root = std::env::temp_dir().join(format!(
            "coperceive-restore-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        std::fs::create_dir_all(root.join("Town03-000")).expect("scene dir");
        assert!(restored_scene_exists(&root, "Town03-000"));
        assert!(!restored_scene_exists(&root, "synth_001"));
        assert!(!restored_scene_exists(&root, "../Town03-000"));
        assert!(!restored_scene_exists(&root, "Town03-000/.."));
        assert!(!restored_scene_exists(&root, "../Town03-000"));
        assert!(!restored_scene_exists(&root, ".hidden"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn layer_defaults_keep_points_and_boxes_on() {
        let defaults = LayerToggles::default();
        assert!(defaults.points);
        assert!(defaults.boxes);
        let parsed: LayerToggles = serde_json::from_str("{}").expect("empty layer toggles");
        assert!(parsed.points);
        assert!(parsed.boxes);
        let explicit: LayerToggles =
            serde_json::from_str(r#"{"points":false,"boxes":false}"#).expect("explicit");
        assert!(!explicit.points && !explicit.boxes);
    }

    #[test]
    fn test_state_serialize_deserialize() {
        let mut visibility = HashMap::new();
        visibility.insert("vehicle1".into(), true);
        visibility.insert("drone1".into(), false);

        let state = AppPersistentState {
            last_data_root: Some("/path/to/data".into()),
            library_root: Some("/path/to/coperceive-data".into()),
            validation_cache: None,
            auto_download_updates: true,
            pending_download: None,
            last_scene_id: Some("Town03-000".into()),
            last_sample_index: Some(42),
            last_timestamp_us: Some(4200000),
            _legacy_timestamp_ns: None,
            last_viewpoint: Some("chase".into()),
            last_loop: Some(false),
            theme_override: Some("light".into()),
            _legacy_last_theme: None,
            reopen_last_session: true,
            agent_visibility: Some(visibility),
            layer_toggles: Some(LayerToggles {
                points: true,
                boxes: false,
                frustums: true,
                map: true,
                axes: false,
                frustum_images: false,
                frustum_opacity: 0.75,
                frustum_image_cameras: Some(vec!["veh_01/cam_front".into()]),
            }),
            selected_camera: Some("vehicle1/cam_front".into()),
            split_view: Some(true),
            panel_collapse: Some(PanelCollapseState {
                left_collapsed: true,
                right_collapsed: false,
                camera_strip_open: false,
            }),
            layout: Some(LayoutSizes {
                left_px: 300.0,
                right_px: 320.0,
                strip_px: 110.0,
            }),
            window_state: Some(WindowState {
                width: 1400.0,
                height: 900.0,
                x: 100.0,
                y: 50.0,
                is_maximized: false,
                monitor: Some("Display 1".into()),
                scale_factor: Some(2.0),
            }),
            recent_roots: vec!["/path/to/data".into(), "/path/to/prev".into()],
        };

        let json = serde_json::to_string(&state).expect("serialize");
        let deserialized: AppPersistentState = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(state, deserialized);
    }

    #[test]
    fn test_legacy_timestamp_ns_migration() {
        let legacy_json = r#"{
            "last_data_root": "/path/to/data",
            "last_timestamp_ns": 5500000000
        }"#;
        let tmp = std::env::temp_dir().join(format!("coperceive-test-state-legacy-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);
        let state_path = tmp.join("state.json");
        fs::write(&state_path, legacy_json).expect("write legacy");

        let loaded = load_state_from_path(&state_path);
        assert_eq!(loaded.last_timestamp_us, Some(5500000));
        let _ = fs::remove_dir_all(&tmp);
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
        assert_eq!(loaded.theme_override, None);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_legacy_last_theme_does_not_set_theme_override() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-theme-migration-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);
        let state_path = tmp.join("state.json");

        // Old state.json with `last_theme: "dark"`
        fs::write(
            &state_path,
            r#"{"last_data_root":"/tmp","last_theme":"dark","last_scene_id":"synth_001"}"#,
        )
        .expect("write old state");

        let loaded = load_state_from_path(&state_path);
        // Migration rule: an old last_theme alone does not set an override!
        assert_eq!(loaded.theme_override, None);

        // When saved back, last_theme is dropped and theme_override is omitted
        save_state_to_path(&loaded, &state_path).expect("save state");
        let raw = fs::read_to_string(&state_path).expect("read migrated state");
        assert!(!raw.contains("last_theme"), "legacy last_theme should not be re-serialized");
        assert!(!raw.contains("theme_override"), "unset theme_override should not be serialized");

        // An explicit theme_override IS loaded and preserved
        fs::write(
            &state_path,
            r#"{"last_data_root":"/tmp","theme_override":"dark","last_scene_id":"synth_001"}"#,
        )
        .expect("write explicit override state");
        let loaded_override = load_state_from_path(&state_path);
        assert_eq!(loaded_override.theme_override, Some("dark".to_string()));

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

        // Empty directory: invalid (missing manifest.json and COMPLETE.json)
        let empty_dir = tmp.join("empty");
        let _ = fs::create_dir_all(&empty_dir);
        let err = validate_dataset_root(&empty_dir).unwrap_err();
        assert!(err.contains("No coperceive scenes in"));
        assert!(err.contains("A scene folder contains manifest.json and COMPLETE.json."));

        // Single scene directory with manifest.json only: invalid without COMPLETE.json
        let single_scene = tmp.join("single_scene");
        let _ = fs::create_dir_all(&single_scene);
        fs::write(single_scene.join("manifest.json"), "{}").expect("write manifest");
        assert!(validate_dataset_root(&single_scene).is_err());
        fs::write(single_scene.join("COMPLETE.json"), "{}").expect("write complete");
        assert!(validate_dataset_root(&single_scene).is_ok());

        // Multi-scene root with child containing manifest.json and COMPLETE.json: valid
        let multi_root = tmp.join("multi_root");
        let child_scene = multi_root.join("scene_001");
        let _ = fs::create_dir_all(&child_scene);
        fs::write(child_scene.join("manifest.json"), "{}").expect("write manifest");
        assert!(validate_dataset_root(&multi_root).is_err());
        fs::write(child_scene.join("COMPLETE.json"), "{}").expect("write complete");
        assert!(validate_dataset_root(&multi_root).is_ok());

        // An import still in <scene>.partial/ is not a scene, even with both markers.
        let partial = multi_root.join("scene_002.partial");
        let _ = fs::create_dir_all(&partial);
        fs::write(partial.join("manifest.json"), "{}").expect("write partial manifest");
        fs::write(partial.join("COMPLETE.json"), "{}").expect("write partial complete");
        assert!(validate_dataset_root(&partial).is_err());
        assert!(validate_dataset_root(&multi_root).is_ok());

        let only_partial = tmp.join("only_partial");
        let only_child = only_partial.join("scene_003.partial");
        let _ = fs::create_dir_all(&only_child);
        fs::write(only_child.join("manifest.json"), "{}").expect("write only manifest");
        fs::write(only_child.join("COMPLETE.json"), "{}").expect("write only complete");
        assert!(validate_dataset_root(&only_partial).is_err());

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_frustum_images_layer_toggle_persistence() {
        let json = r#"{
            "layer_toggles": {
                "points": true,
                "boxes": true,
                "frustums": true,
                "map": true,
                "axes": true,
                "frustum_images": false,
                "frustum_opacity": 0.65,
                "frustum_image_cameras": ["veh_01/cam_front", "uav_01/cam_down"]
            }
        }"#;
        let parsed: AppPersistentState = serde_json::from_str(json).expect("parse layer toggles");
        let toggles = parsed.layer_toggles.expect("layer toggles present");
        assert_eq!(toggles.frustum_images, false);
        assert_eq!(toggles.frustum_opacity, 0.65);
        assert_eq!(
            toggles.frustum_image_cameras,
            Some(vec!["veh_01/cam_front".into(), "uav_01/cam_down".into()])
        );
    }

    #[test]
    fn test_window_geometry_round_trip_scale_1_and_2() {
        let initial_logical_x = 132.0;
        let initial_logical_y = 132.0;
        let initial_logical_w = 1400.0;
        let initial_logical_h = 900.0;

        // Test scale 1.0 round trip across multiple simulated launches
        {
            let scale_1 = 1.0;
            let monitor_1 = MonitorInfo {
                name: Some("Standard 1080p Display".into()),
                x: 0.0,
                y: 0.0,
                width: 1920.0,
                height: 1080.0,
                scale_factor: scale_1,
                is_primary: true,
            };
            let monitors_1 = vec![monitor_1.clone()];

            let mut current_state = WindowState {
                x: initial_logical_x,
                y: initial_logical_y,
                width: initial_logical_w,
                height: initial_logical_h,
                is_maximized: false,
                monitor: monitor_1.name.clone(),
                scale_factor: Some(scale_1),
            };

            for relaunch in 1..=5 {
                let (phys_x, phys_y, phys_w, phys_h) = current_state.to_physical(scale_1);
                assert_eq!(phys_x, 132);
                assert_eq!(phys_y, 132);
                assert_eq!(phys_w, 1400);
                assert_eq!(phys_h, 900);

                let saved = WindowState::from_physical(
                    phys_x,
                    phys_y,
                    phys_w,
                    phys_h,
                    scale_1,
                    monitor_1.name.clone(),
                    false,
                );

                let restored = resolve_window_geometry(&saved, &monitors_1);
                assert_eq!(restored.x, initial_logical_x, "scale 1.0 x drifted on relaunch {}", relaunch);
                assert_eq!(restored.y, initial_logical_y, "scale 1.0 y drifted on relaunch {}", relaunch);
                assert_eq!(restored.width, initial_logical_w, "scale 1.0 w drifted on relaunch {}", relaunch);
                assert_eq!(restored.height, initial_logical_h, "scale 1.0 h drifted on relaunch {}", relaunch);
                assert_eq!(restored.scale_factor, Some(scale_1));
                current_state = restored;
            }
        }

        // Test scale 2.0 round trip across multiple simulated launches (prevents y = 132 -> 264 -> 528 drift)
        {
            let scale_2 = 2.0;
            let monitor_2 = MonitorInfo {
                name: Some("Built-in Retina Display".into()),
                x: 0.0,
                y: 0.0,
                width: 1728.0,
                height: 1117.0,
                scale_factor: scale_2,
                is_primary: true,
            };
            let monitors_2 = vec![monitor_2.clone()];

            let mut current_state = WindowState {
                x: initial_logical_x,
                y: initial_logical_y,
                width: initial_logical_w,
                height: initial_logical_h,
                is_maximized: false,
                monitor: monitor_2.name.clone(),
                scale_factor: Some(scale_2),
            };

            for relaunch in 1..=5 {
                let (phys_x, phys_y, phys_w, phys_h) = current_state.to_physical(scale_2);
                assert_eq!(phys_x, 264);
                assert_eq!(phys_y, 264);
                assert_eq!(phys_w, 2800);
                assert_eq!(phys_h, 1800);

                let saved = WindowState::from_physical(
                    phys_x,
                    phys_y,
                    phys_w,
                    phys_h,
                    scale_2,
                    monitor_2.name.clone(),
                    false,
                );

                // Logical coordinates must remain exact: 132, 132, 1400, 900
                assert_eq!(saved.x, initial_logical_x);
                assert_eq!(saved.y, initial_logical_y);
                assert_eq!(saved.width, initial_logical_w);
                assert_eq!(saved.height, initial_logical_h);

                let restored = resolve_window_geometry(&saved, &monitors_2);
                assert_eq!(restored.x, initial_logical_x, "scale 2.0 x drifted on relaunch {}", relaunch);
                assert_eq!(restored.y, initial_logical_y, "scale 2.0 y drifted on relaunch {}", relaunch);
                assert_eq!(restored.width, initial_logical_w, "scale 2.0 w drifted on relaunch {}", relaunch);
                assert_eq!(restored.height, initial_logical_h, "scale 2.0 h drifted on relaunch {}", relaunch);
                assert_eq!(restored.scale_factor, Some(scale_2));
                current_state = restored;
            }
        }
    }

    #[test]
    fn test_window_geometry_clamping() {
        let primary = MonitorInfo {
            name: Some("Primary Display".into()),
            x: 0.0,
            y: 0.0,
            width: 1920.0,
            height: 1080.0,
            scale_factor: 1.0,
            is_primary: true,
        };
        let external = MonitorInfo {
            name: Some("External 4K".into()),
            x: 1920.0,
            y: 0.0,
            width: 2560.0,
            height: 1440.0,
            scale_factor: 2.0,
            is_primary: false,
        };
        let monitors = vec![primary.clone(), external.clone()];

        // Case 1: Partial overflow on existing monitor (>50% overlap) clamps fully inside visible frame
        {
            let saved = WindowState {
                x: 1100.0,
                y: 100.0,
                width: 1000.0,
                height: 800.0,
                is_maximized: false,
                monitor: primary.name.clone(),
                scale_factor: Some(1.0),
            };
            let resolved = resolve_window_geometry(&saved, &monitors);
            // 1100 + 1000 = 2100 > 1920 -> clamped to 1920 - 1000 = 920.0
            assert_eq!(resolved.x, 920.0);
            assert_eq!(resolved.y, 100.0);
            assert_eq!(resolved.width, 1000.0);
            assert_eq!(resolved.height, 800.0);
            assert_eq!(resolved.monitor, primary.name);
        }

        // Case 2: Negative coordinates (overflow top-left, >50% overlap) clamped to 0.0
        {
            let saved = WindowState {
                x: -100.0,
                y: -50.0,
                width: 1000.0,
                height: 800.0,
                is_maximized: false,
                monitor: primary.name.clone(),
                scale_factor: Some(1.0),
            };
            let resolved = resolve_window_geometry(&saved, &monitors);
            assert_eq!(resolved.x, 0.0);
            assert_eq!(resolved.y, 0.0);
            assert_eq!(resolved.width, 1000.0);
            assert_eq!(resolved.height, 800.0);
        }

        // Case 3: Mostly off-screen (<50% overlap) centres on primary monitor
        {
            let saved = WindowState {
                x: 1800.0,
                y: 200.0,
                width: 1000.0,
                height: 800.0,
                is_maximized: false,
                monitor: primary.name.clone(),
                scale_factor: Some(1.0),
            };
            let resolved = resolve_window_geometry(&saved, &monitors);
            // Centred on primary (1920x1080):
            // x = (1920 - 1000) / 2 = 460.0
            // y = (1080 - 800) / 2 = 140.0
            assert_eq!(resolved.x, 460.0);
            assert_eq!(resolved.y, 140.0);
            assert_eq!(resolved.width, 1000.0);
            assert_eq!(resolved.height, 800.0);
        }

        // Case 4: Saved monitor is gone (disconnected) centres on primary monitor
        {
            let saved = WindowState {
                x: 2100.0,
                y: 200.0,
                width: 1200.0,
                height: 800.0,
                is_maximized: false,
                monitor: Some("Disconnected Display".into()),
                scale_factor: Some(2.0),
            };
            let resolved = resolve_window_geometry(&saved, &monitors);
            // Centred on primary:
            // x = (1920 - 1200) / 2 = 360.0
            // y = (1080 - 800) / 2 = 140.0
            assert_eq!(resolved.x, 360.0);
            assert_eq!(resolved.y, 140.0);
            assert_eq!(resolved.width, 1200.0);
            assert_eq!(resolved.height, 800.0);
            assert_eq!(resolved.monitor, primary.name);
        }
    }

    #[test]
    fn test_layout_sizes_default_and_clamp() {
        let mut layout = LayoutSizes::default();
        assert_eq!(layout.left_px, DEFAULT_LEFT_PX);
        assert_eq!(layout.right_px, DEFAULT_RIGHT_PX);
        assert_eq!(layout.strip_px, DEFAULT_STRIP_PX);

        // Clamping lower bounds
        layout.left_px = 50.0;
        layout.right_px = 100.0;
        layout.strip_px = 20.0;
        layout.clamp();
        assert_eq!(layout.left_px, MIN_LEFT_PX);
        assert_eq!(layout.right_px, MIN_RIGHT_PX);
        assert_eq!(layout.strip_px, MIN_STRIP_PX);

        // Clamping upper bounds
        layout.left_px = 999.0;
        layout.right_px = 999.0;
        layout.strip_px = 999.0;
        layout.clamp();
        assert_eq!(layout.left_px, MAX_LEFT_PX);
        assert_eq!(layout.right_px, MAX_RIGHT_PX);
        assert_eq!(layout.strip_px, MAX_STRIP_PX);
    }

    #[test]
    fn test_layout_sizes_state_round_trip() {
        let json_data = serde_json::json!({
            "layout": {
                "left_px": 310.0,
                "right_px": 350.0,
                "strip_px": 120.0
            }
        });
        let state: AppPersistentState = serde_json::from_value(json_data).unwrap();
        assert!(state.layout.is_some());
        let layout = state.layout.unwrap();
        assert_eq!(layout.left_px, 310.0);
        assert_eq!(layout.right_px, 350.0);
        assert_eq!(layout.strip_px, 120.0);

        let serialized = serde_json::to_string(&layout).unwrap();
        let deserialized: LayoutSizes = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized, layout);
    }
}
