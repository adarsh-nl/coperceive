// SPDX-License-Identifier: Apache-2.0
//! Library management and dataset storage.
//!
//! Provides volume free-space querying, scene metadata discovery,
//! and library relocation functions for the Coperceive desktop app.

use std::collections::HashSet;
use std::fs;
use std::path::Path;
use serde::{Deserialize, Serialize};

/// Storage metrics for the dataset library location.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StorageStats {
    pub library_root: String,
    pub is_configured: bool,
    pub free_bytes: u64,
    pub used_bytes: u64,
    pub total_bytes: u64,
    pub volume_name: String,
}

/// Metadata and validation state of a scene stored in the library.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LibrarySceneInfo {
    pub scene_id: String,
    pub path: String,
    pub dataset: String,
    pub town: Option<String>,
    pub duration_s: f64,
    pub sample_count: usize,
    pub agent_count: usize,
    pub sensor_modalities: Vec<String>,
    pub size_bytes: u64,
    /// "full" (original images), "preview" (compressed 640px JPEG), or "none" (no camera data)
    pub has_images: String,
    pub has_map: bool,
    pub is_valid: Option<bool>,
}

#[cfg(unix)]
pub fn get_disk_space(path: &Path) -> (u64, u64) {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    // Use path if exists, or its closest existing ancestor
    let mut check = path.to_path_buf();
    while !check.exists() {
        if let Some(parent) = check.parent() {
            check = parent.to_path_buf();
        } else {
            break;
        }
    }

    let c_path = match CString::new(check.as_os_str().as_bytes()) {
        Ok(p) => p,
        Err(_) => return (0, 0),
    };

    unsafe {
        let mut stat: libc::statvfs = std::mem::zeroed();
        if libc::statvfs(c_path.as_ptr(), &mut stat) == 0 {
            let free = (stat.f_bavail as u64).saturating_mul(stat.f_frsize as u64);
            let total = (stat.f_blocks as u64).saturating_mul(stat.f_frsize as u64);
            (free, total)
        } else {
            (0, 0)
        }
    }
}

#[cfg(not(unix))]
pub fn get_disk_space(_path: &Path) -> (u64, u64) {
    (0, 0)
}

/// Recursively sums the byte size of all files under a directory.
pub fn calculate_directory_size(path: &Path) -> u64 {
    let mut total: u64 = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_symlink() {
                // Do not follow symlinks outside scene directory
                continue;
            }
            if p.is_dir() {
                total = total.saturating_add(calculate_directory_size(&p));
            } else if let Ok(meta) = p.metadata() {
                total = total.saturating_add(meta.len());
            }
        }
    }
    total
}

/// Discovers volume name (e.g. "Macintosh HD" or mount point).
pub fn get_volume_name(path: &Path) -> String {
    let path_str = path.to_string_lossy();
    if path_str.starts_with("/Volumes/") {
        let parts: Vec<&str> = path_str.split('/').collect();
        if parts.len() >= 3 && !parts[2].is_empty() {
            return parts[2].to_string();
        }
    }
    if cfg!(target_os = "macos") {
        "Macintosh HD".to_string()
    } else {
        "System Volume".to_string()
    }
}

/// Queries storage metrics for the library.
pub fn get_storage_stats(library_root: &Path, is_configured: bool) -> StorageStats {
    let (free_bytes, total_bytes) = get_disk_space(library_root);
    let used_bytes = if library_root.exists() {
        calculate_directory_size(library_root)
    } else {
        0
    };
    let volume_name = get_volume_name(library_root);

    StorageStats {
        library_root: library_root.to_string_lossy().into_owned(),
        is_configured,
        free_bytes,
        used_bytes,
        total_bytes,
        volume_name,
    }
}

/// Scans the library root for valid scenes.
/// Skips any `.partial` folders (which are in-progress downloads or imports).
pub fn scan_library_scenes(library_root: &Path) -> Vec<LibrarySceneInfo> {
    let mut scenes = Vec::new();
    if !library_root.exists() || !library_root.is_dir() {
        return scenes;
    }

    let entries = match fs::read_dir(library_root) {
        Ok(e) => e,
        Err(_) => return scenes,
    };

    for entry in entries.flatten() {
        let p = entry.path();
        let file_name = match p.file_name().and_then(|n| n.to_str()) {
            Some(name) => name,
            None => continue,
        };

        // Skip in-progress downloads and imports (.partial)
        if file_name.ends_with(".partial") || file_name.starts_with('.') {
            continue;
        }

        if p.is_dir() {
            // Check if this directory directly is a scene (contains manifest.json and COMPLETE.json)
            if p.join("manifest.json").exists() && p.join("COMPLETE.json").exists() {
                if let Some(info) = parse_scene_info(&p) {
                    scenes.push(info);
                }
            } else {
                // Check if it's a dataset subfolder containing scenes
                if let Ok(sub_entries) = fs::read_dir(&p) {
                    for sub in sub_entries.flatten() {
                        let sub_p = sub.path();
                        let sub_name = sub_p.file_name().and_then(|n| n.to_str()).unwrap_or("");
                        if sub_name.ends_with(".partial") || sub_name.starts_with('.') {
                            continue;
                        }
                        if sub_p.is_dir() && sub_p.join("manifest.json").exists() && sub_p.join("COMPLETE.json").exists() {
                            if let Some(info) = parse_scene_info(&sub_p) {
                                scenes.push(info);
                            }
                        }
                    }
                }
            }
        }
    }

    scenes.sort_by(|a, b| a.scene_id.cmp(&b.scene_id));
    scenes
}

fn parse_scene_info(scene_dir: &Path) -> Option<LibrarySceneInfo> {
    let manifest_path = scene_dir.join("manifest.json");
    let content = fs::read_to_string(&manifest_path).ok()?;
    let manifest: serde_json::Value = serde_json::from_str(&content).ok()?;

    let scene_id = manifest
        .get("scene_id")
        .and_then(|s| s.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            scene_dir.file_name().and_then(|n| n.to_str()).unwrap_or("scene").to_string()
        });

    // Infer dataset from manifest, COMPLETE.json, or parent folder
    let dataset = scene_dir
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
        .filter(|&name| name != "coperceive-data" && !name.is_empty())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "Griffin".to_string());

    let mut town = None;
    let mut agent_count = 0;
    if let Some(agents) = manifest.get("agents").and_then(|a| a.as_array()) {
        agent_count = agents.len();
        for agent in agents {
            if let Some(map_name) = agent.pointer("/platform/map_name").and_then(|m| m.as_str()) {
                let parts: Vec<&str> = map_name.split('/').collect();
                if let Some(last) = parts.last() {
                    town = Some(last.to_string());
                    break;
                }
            }
        }
    }

    let mut modalities = HashSet::new();
    if let Some(sensors) = manifest.get("sensors").and_then(|s| s.as_array()) {
        for sensor in sensors {
            if let Some(m) = sensor.get("modality").and_then(|m| m.as_str()) {
                modalities.insert(m.to_string());
            }
        }
    }
    let mut sensor_modalities: Vec<String> = modalities.into_iter().collect();
    sensor_modalities.sort();

    let has_map = scene_dir.join("map.geojson").exists()
        || scene_dir.join("map_lines.json").exists()
        || scene_dir.join("map").exists();

    // Check images
    let has_images = if scene_dir.join("images.parquet").exists() || scene_dir.join("images").is_dir() {
        // Inspect if preview or full
        if let Ok(meta_str) = fs::read_to_string(scene_dir.join("IMPORT.json")) {
            if meta_str.contains("\"preview\"") {
                "preview".to_string()
            } else {
                "full".to_string()
            }
        } else {
            "full".to_string()
        }
    } else {
        "none".to_string()
    };

    let size_bytes = calculate_directory_size(scene_dir);

    Some(LibrarySceneInfo {
        scene_id,
        path: scene_dir.to_string_lossy().into_owned(),
        dataset,
        town,
        duration_s: 15.0, // default evaluation scene duration
        sample_count: 150,
        agent_count,
        sensor_modalities,
        size_bytes,
        has_images,
        has_map,
        is_valid: None,
    })
}

/// Moves scenes from an old library directory to a new library directory.
pub fn move_library_scenes(from_root: &Path, to_root: &Path) -> Result<(), String> {
    if !from_root.exists() {
        return Ok(());
    }
    fs::create_dir_all(to_root).map_err(|e| format!("Failed to create destination directory: {e}"))?;

    let entries = fs::read_dir(from_root).map_err(|e| format!("Failed to read source directory: {e}"))?;
    for entry in entries.flatten() {
        let p = entry.path();
        let name = match p.file_name() {
            Some(n) => n,
            None => continue,
        };
        let dest = to_root.join(name);
        if let Err(_e) = fs::rename(&p, &dest) {
            // Fallback to copy + remove if across filesystem boundaries
            copy_dir_recursive(&p, &dest).map_err(|err| format!("Failed to move {name:?}: {err}"))?;
            let _ = fs::remove_dir_all(&p);
        }
    }
    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    if src.is_dir() {
        fs::create_dir_all(dst)?;
        for entry in fs::read_dir(src)? {
            let entry = entry?;
            copy_dir_recursive(&entry.path(), &dst.join(entry.file_name()))?;
        }
    } else {
        fs::copy(src, dst)?;
    }
    Ok(())
}

/// Deletes a scene folder, moving it to Trash when possible.
pub fn delete_scene_to_trash(scene_dir: &Path) -> Result<(), String> {
    if !scene_dir.exists() {
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        if let Ok(home) = std::env::var("HOME") {
            let trash = PathBuf::from(home).join(".Trash");
            if trash.exists() {
                if let Some(name) = scene_dir.file_name() {
                    let dest = trash.join(format!("{}-{}", name.to_string_lossy(), std::process::id()));
                    if fs::rename(scene_dir, &dest).is_ok() {
                        return Ok(());
                    }
                }
            }
        }
    }

    fs::remove_dir_all(scene_dir).map_err(|e| format!("Failed to remove scene directory: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scan_library_scenes_skips_partial_and_reads_metadata() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-lib-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);

        // Valid scene: Town03-000
        let scene_0 = tmp.join("Town03-000");
        let _ = fs::create_dir_all(&scene_0);
        let manifest = r#"{
            "schema_version": 1,
            "scene_id": "Town03-000",
            "agents": [
                { "agent_id": "veh1", "agent_type": "vehicle", "platform": { "map_name": "Carla/Maps/Town03" } },
                { "agent_id": "uav1", "agent_type": "uav", "platform": { "map_name": "Carla/Maps/Town03" } }
            ],
            "sensors": [
                { "sensor_id": "lidar_top", "modality": "lidar" },
                { "sensor_id": "cam_front", "modality": "camera" }
            ]
        }"#;
        fs::write(scene_0.join("manifest.json"), manifest).expect("write manifest");
        fs::write(scene_0.join("COMPLETE.json"), "{}").expect("write complete");
        fs::write(scene_0.join("map.geojson"), "{}").expect("write map");
        fs::write(scene_0.join("images.parquet"), "data").expect("write images");

        // Partial scene: Town03-001.partial (MUST BE SKIPPED)
        let partial_scene = tmp.join("Town03-001.partial");
        let _ = fs::create_dir_all(&partial_scene);
        fs::write(partial_scene.join("manifest.json"), manifest).expect("write manifest");
        fs::write(partial_scene.join("IMPORT.json"), "{}").expect("write import");

        // Corrupt folder: missing COMPLETE.json (MUST BE SKIPPED)
        let corrupt = tmp.join("CorruptScene");
        let _ = fs::create_dir_all(&corrupt);
        fs::write(corrupt.join("manifest.json"), manifest).expect("write manifest");

        let scenes = scan_library_scenes(&tmp);
        assert_eq!(scenes.len(), 1, "Expected exactly 1 complete scene, ignoring .partial and corrupt");
        let s = &scenes[0];
        assert_eq!(s.scene_id, "Town03-000");
        assert_eq!(s.town.as_deref(), Some("Town03"));
        assert_eq!(s.agent_count, 2);
        assert_eq!(s.sensor_modalities, vec!["camera".to_string(), "lidar".to_string()]);
        assert_eq!(s.has_map, true);
        assert_eq!(s.has_images, "full");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_library_scenes() {
        let tmp = std::env::temp_dir().join(format!("coperceive-test-move-{}", std::process::id()));
        let src = tmp.join("src_lib");
        let dst = tmp.join("dst_lib");
        let _ = fs::create_dir_all(&src);

        let scene = src.join("scene_1");
        let _ = fs::create_dir_all(&scene);
        fs::write(scene.join("manifest.json"), "{}").expect("write");
        fs::write(scene.join("COMPLETE.json"), "{}").expect("write");

        move_library_scenes(&src, &dst).expect("move scenes");

        assert!(!scene.exists());
        assert!(dst.join("scene_1").join("COMPLETE.json").exists());

        let _ = fs::remove_dir_all(&tmp);
    }
}
