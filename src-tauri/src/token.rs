// SPDX-License-Identifier: Apache-2.0

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

#[derive(Debug, PartialEq, Eq)]
pub enum TokenSecurityError {
    FileNotFound,
    LoosePermissions(u32),
    IoError(String),
}

impl std::fmt::Display for TokenSecurityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::FileNotFound => write!(f, "token file does not exist"),
            Self::LoosePermissions(mode) => {
                write!(f, "token file has loose permissions {:#o}; must be mode 0600 or 0400", mode)
            }
            Self::IoError(err) => write!(f, "io error reading token: {}", err),
        }
    }
}

impl std::error::Error for TokenSecurityError {}

/// Generates a cryptographically secure 128-bit hex token (32 lowercase hex characters).
pub fn generate_secure_token() -> std::io::Result<String> {
    let mut bytes = [0u8; 16];
    let mut file = File::open("/dev/urandom")?;
    file.read_exact(&mut bytes)?;
    let mut hex = String::with_capacity(32);
    for b in bytes {
        use std::fmt::Write as _;
        let _ = write!(hex, "{:02x}", b);
    }
    Ok(hex)
}

/// Writes the token to a file with strict 0600 permissions (read/write by owner only).
pub fn write_token_file(path: &Path, token: &str) -> std::io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    options.mode(0o600);
    let mut file = options.open(path)?;
    file.write_all(token.as_bytes())?;
    file.write_all(b"\n")?;
    file.flush()?;
    Ok(())
}

/// Verifies that the token file has strict permissions (mode 0600 or 0400).
pub fn verify_token_file_permissions(path: &Path) -> Result<(), TokenSecurityError> {
    if !path.is_file() {
        return Err(TokenSecurityError::FileNotFound);
    }
    let metadata = path.metadata().map_err(|e| TokenSecurityError::IoError(e.to_string()))?;
    let mode = metadata.permissions().mode() & 0o777;
    if (mode & 0o077) != 0 {
        return Err(TokenSecurityError::LoosePermissions(mode));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_generate_secure_token() {
        let t1 = generate_secure_token().expect("token generation failed");
        let t2 = generate_secure_token().expect("token generation failed");
        assert_eq!(t1.len(), 32);
        assert_eq!(t2.len(), 32);
        assert_ne!(t1, t2);
        assert!(t1.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_token_file_strict_permissions() {
        let unique = format!("test-coperceive-token-{}-{}", std::process::id(), generate_secure_token().unwrap());
        let path = std::env::temp_dir().join(unique);
        write_token_file(&path, "secret-token-1234").unwrap();

        assert_eq!(verify_token_file_permissions(&path), Ok(()));

        // Loosen permissions to 0o644
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        match verify_token_file_permissions(&path) {
            Err(TokenSecurityError::LoosePermissions(m)) => assert_eq!(m, 0o644),
            other => panic!("expected LoosePermissions, got {:?}", other),
        }

        let _ = fs::remove_file(&path);
    }
}
