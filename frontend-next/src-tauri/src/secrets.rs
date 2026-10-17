//! OS keychain (Windows Credential Manager / macOS Keychain / Secret Service) for the refresh token of
//! each server and the generated administrator password (方案 §3.5 “安全”, §4.4).

use crate::error::{DesktopError, Result};

pub const SERVICE: &str = "com.formunex.desktop";
pub const ADMIN_PASSWORD: &str = "admin-password";

/// Only these entries may be read / written from the UI.
pub fn allowed_key(key: &str) -> bool {
    key == ADMIN_PASSWORD || key.strip_prefix("refresh:").is_some_and(|base| base.starts_with("http"))
}

fn entry(key: &str) -> Result<keyring::Entry> {
    if !allowed_key(key) {
        return Err(DesktopError::Keychain(format!("不允许的条目：{key}")));
    }
    keyring::Entry::new(SERVICE, key).map_err(|e| DesktopError::Keychain(e.to_string()))
}

pub fn get(key: &str) -> Result<Option<String>> {
    match entry(key)?.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(DesktopError::Keychain(e.to_string())),
    }
}

pub fn set(key: &str, value: &str) -> Result<()> {
    entry(key)?.set_password(value).map_err(|e| DesktopError::Keychain(e.to_string()))
}

pub fn delete(key: &str) -> Result<()> {
    match entry(key)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(DesktopError::Keychain(e.to_string())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_known_entries() {
        assert!(allowed_key("admin-password"));
        assert!(allowed_key("refresh:https://fx.example.com"));
        assert!(allowed_key("refresh:http://127.0.0.1:8000"));
        assert!(!allowed_key("refresh:"));
        assert!(!allowed_key("anything"));
        assert!(matches!(get("anything"), Err(DesktopError::Keychain(_))));
    }
}
