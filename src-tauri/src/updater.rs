// SPDX-License-Identifier: Apache-2.0
//! Desktop Application Updater
//!
//! Provides update checking outcome models, test-mode endpoint override resolution,
//! and quiet background check handling for macOS Coperceive.

use serde::{Deserialize, Serialize};

pub const DEFAULT_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDlBQjI5NEI1NkZDM0M0NDQKUldSRXhNTnZ0WlN5bXVJWXpKOWIrNHJ6TEdrRkNPYTlxWWFENnIrbkZuQ2VLekk3OXhrN2NoTGEK";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "status", rename_all = "lowercase")]
pub enum UpdateCheckOutcome {
    Available {
        version: String,
        current_version: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        notes: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub_date: Option<String>,
    },
    UpToDate {
        current_version: String,
    },
    Error {
        error: String,
        is_network: bool,
    },
}

/// Resolves an update endpoint override URL, if allowed.
///
/// Overrides (from request body or `COPERCEIVE_UPDATE_URL`) are strictly honoured
/// ONLY when `coperceive_desktop::test_seams::test_env_active()` is true.
/// In normal runs, any override is ignored and `None` is returned so the configured
/// production endpoint in `tauri.conf.json` is used.
pub fn resolve_update_endpoint_override(override_url_opt: Option<&str>) -> Option<String> {
    if !crate::test_seams::test_env_active() {
        return None;
    }
    override_url_opt
        .map(|s| s.trim().to_string())
        .or_else(|| std::env::var("COPERCEIVE_UPDATE_URL").ok().map(|s| s.trim().to_string()))
        .filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tauri_conf_has_no_insecure_transport_protocol() {
        let conf_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
        let content = std::fs::read_to_string(&conf_path).expect("read tauri.conf.json");
        assert!(
            !content.contains("dangerousInsecureTransportProtocol"),
            "Committed tauri.conf.json must not enable dangerousInsecureTransportProtocol"
        );
    }

    #[test]
    fn test_endpoint_overrides_honoured_only_when_test_env_active() {
        std::env::remove_var("COPERCEIVE_TEST_ENV");
        std::env::remove_var("COPERCEIVE_TEST_MARKER_FILE");
        std::env::set_var("COPERCEIVE_UPDATE_URL", "http://evil.example.com/manifest.json");

        assert_eq!(
            resolve_update_endpoint_override(Some("http://override.example.com/manifest.json")),
            None
        );
        assert_eq!(resolve_update_endpoint_override(None), None);

        std::env::set_var("COPERCEIVE_TEST_ENV", "1");
        assert_eq!(
            resolve_update_endpoint_override(Some("http://127.0.0.1:8080/latest.json")),
            Some("http://127.0.0.1:8080/latest.json".to_string())
        );
        std::env::set_var("COPERCEIVE_UPDATE_URL", "http://127.0.0.1:9090/latest.json");
        assert_eq!(
            resolve_update_endpoint_override(None),
            Some("http://127.0.0.1:9090/latest.json".to_string())
        );
        std::env::remove_var("COPERCEIVE_UPDATE_URL");
        std::env::remove_var("COPERCEIVE_TEST_ENV");
    }

    #[test]
    fn test_outcome_serialization() {
        let available = UpdateCheckOutcome::Available {
            version: "0.2.0".into(),
            current_version: "0.1.0".into(),
            notes: Some("Notes".into()),
            pub_date: None,
        };
        let json = serde_json::to_value(&available).unwrap();
        assert_eq!(json["status"], "available");
        assert_eq!(json["version"], "0.2.0");

        let uptodate = UpdateCheckOutcome::UpToDate {
            current_version: "0.1.0".into(),
        };
        let json_uptodate = serde_json::to_value(&uptodate).unwrap();
        assert_eq!(json_uptodate["status"], "uptodate");

        let error = UpdateCheckOutcome::Error {
            error: "Connection failed".into(),
            is_network: true,
        };
        let json_err = serde_json::to_value(&error).unwrap();
        assert_eq!(json_err["status"], "error");
        assert_eq!(json_err["is_network"], true);
    }
}
