//! OS keychain (Windows Credential Manager / macOS Keychain / Secret Service) for the refresh token of
//! each server and the generated administrator password (方案 §3.5 “安全”, §4.4).

use crate::error::{DesktopError, Result};

pub const SERVICE: &str = "com.formunex.desktop";
pub const ADMIN_PASSWORD: &str = "admin-password";

/// P1-E14: the allowlist is split by direction. The generated admin
/// password is write-only from the UI's point of view: create_admin
/// returns it once, and nothing may read it back through keychain_get.
/// P3-E12: a refresh entry must name a real http(s) origin — the old
/// `starts_with("http")` also accepted `httpx://…` and `httpfoo`.
fn allowed_read_key(key: &str) -> bool {
    key.strip_prefix("refresh:")
        .is_some_and(|base| base.starts_with("http://") || base.starts_with("https://"))
}

fn allowed_write_key(key: &str) -> bool {
    key == ADMIN_PASSWORD || allowed_read_key(key)
}

fn entry(key: &str, write: bool) -> Result<keyring::Entry> {
    let allowed = if write { allowed_write_key(key) } else { allowed_read_key(key) };
    if !allowed {
        return Err(DesktopError::Keychain(format!("不允许的条目：{key}")));
    }
    keyring::Entry::new(SERVICE, key).map_err(|e| DesktopError::Keychain(e.to_string()))
}

pub fn get(key: &str) -> Result<Option<String>> {
    match entry(key, false)?.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(DesktopError::Keychain(e.to_string())),
    }
}

pub fn set(key: &str, value: &str) -> Result<()> {
    entry(key, true)?.set_password(value).map_err(|e| DesktopError::Keychain(e.to_string()))
}

pub fn delete(key: &str) -> Result<()> {
    match entry(key, true)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(DesktopError::Keychain(e.to_string())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_entries() {
        // Write side: the admin password may be stored, never read back.
        assert!(allowed_write_key("admin-password"));
        assert!(!allowed_read_key("admin-password"));
        assert!(allowed_read_key("refresh:https://fx.example.com"));
        assert!(allowed_write_key("refresh:https://fx.example.com"));
        assert!(allowed_read_key("refresh:http://127.0.0.1:8000"));
        // P3-E12: the scheme must be exactly http(s).
        assert!(!allowed_read_key("refresh:httpx://fx.example.com"));
        assert!(!allowed_read_key("refresh:httpfoo"));
        assert!(!allowed_read_key("refresh:"));
        assert!(!allowed_read_key("anything"));
        assert!(matches!(get("anything"), Err(DesktopError::Keychain(_))));
        assert!(matches!(get("admin-password"), Err(DesktopError::Keychain(_))));
    }
}
