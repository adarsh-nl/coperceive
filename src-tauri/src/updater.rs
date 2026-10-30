// SPDX-License-Identifier: Apache-2.0
//! Desktop Application Updater
//!
//! Provides update checking, signature verification with minisign,
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

#[cfg(target_os = "macos")]
pub fn decode_minisign_string(s: &str) -> Result<String, String> {
    if s.contains("untrusted comment") {
        Ok(s.to_string())
    } else {
        use base64::Engine;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(s.trim())
            .map_err(|e| format!("Base64 decode error: {e}"))?;
        String::from_utf8(bytes).map_err(|e| format!("UTF-8 decode error: {e}"))
    }
}

/// Verifies package data bytes against a minisign signature and public key.
///
/// Rejects signatures that are invalid, tampered, or do not match the public key.
#[cfg(target_os = "macos")]
pub fn verify_package_signature(
    data: &[u8],
    signature_str: &str,
    pubkey_str: &str,
) -> Result<(), String> {
    use minisign_verify::{PublicKey, Signature};

    let pubkey_decoded = decode_minisign_string(pubkey_str)?;
    let public_key = PublicKey::decode(&pubkey_decoded)
        .map_err(|e| format!("Failed to parse public key: {e}"))?;

    let sig_decoded = decode_minisign_string(signature_str)?;
    let signature = Signature::decode(&sig_decoded)
        .map_err(|e| format!("Failed to parse signature: {e}"))?;

    public_key
        .verify(data, &signature, true)
        .map_err(|e| format!("Minisign verification failed: {e}"))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PUBLIC_KEY: &str = "untrusted comment: minisign public key: A49EF4746FA6E6D7\nRWTX5qZvdPSepDGWg2bz8n6ArQnBMwgQK1geVosorcbLzcunD+KJ+cYe";
    const SIGNATURE: &str = "untrusted comment: signature from tauri secret key\nRUTX5qZvdPSepPF2zsjvP/L/TQtBLRnWRvaq+4omK1UEugtQp/USrGiI1v762sDy3id3aor7bRC9ETyeot6lMHHrWoZ5hjUM1Qc=\ntrusted comment: timestamp:1791649051\tfile:payload\tversion:2.0.0\nDDq2CLOEl5cQmC2eJRHlnZPVViOZsu1CjpPKQoei4A/XBfkDqGO0B+QMrcrHgEQ5qV3mne75tdFWhJpqUXh9AA==";
    const PAYLOAD: &[u8] = b"test";

    #[test]
    #[cfg(target_os = "macos")]
    fn test_valid_signature_is_accepted() {
        let result = verify_package_signature(PAYLOAD, SIGNATURE, PUBLIC_KEY);
        assert!(result.is_ok(), "Expected valid signature to succeed: {result:?}");
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn test_tampered_payload_is_rejected() {
        let tampered = b"test-tampered-content";
        let result = verify_package_signature(tampered, SIGNATURE, PUBLIC_KEY);
        assert!(result.is_err(), "Expected tampered payload to fail verification");
        let err = result.unwrap_err();
        assert!(err.contains("verification failed") || err.contains("Minisign"), "{err}");
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn test_bad_signature_is_rejected() {
        let bad_sig = "untrusted comment: signature\nRUTX5qZvdPSepPF2zsjvP/L/TQtBLRnWRvaq+4omK1UEugtQp/USrGiI1v762sDy3id3aor7bRC9ETyeot6lMHHrWoZ5hjUM1Qc=\ntrusted comment: bad\nDDq2CLOEl5cQmC2eJRHlnZPVViOZsu1CjpPKQoei4A/XBfkDqGO0B+QMrcrHgEQ5qV3mne75tdFWhJpqUXh9AA==";
        let result = verify_package_signature(PAYLOAD, bad_sig, PUBLIC_KEY);
        assert!(result.is_err(), "Expected bad signature to be rejected");
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn test_corrupt_signature_string_is_rejected() {
        let corrupt_sig = "not-a-valid-minisign-signature";
        let result = verify_package_signature(PAYLOAD, corrupt_sig, PUBLIC_KEY);
        assert!(result.is_err(), "Expected corrupt signature to be rejected");
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
