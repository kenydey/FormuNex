//! Desktop settings (方案 §3.5 “连接切换”): local Docker stack or a remote FormuNex server.
//!
//! Stored as `desktop.json` in the app config directory. Secrets never live here: database / graph
//! passwords and the JWT secret are in the stack's `.env` (app data directory, owner-only), the
//! administrator password and refresh tokens in the OS keychain.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::{DesktopError, Result};

pub const CONFIG_FILE: &str = "desktop.json";
pub const DEFAULT_IMAGE: &str = "ghcr.io/kenydey/formunex-backend";
pub const DEFAULT_API_PORT: u16 = 8000;

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    /// Run the full stack on this computer with Docker Desktop.
    Local,
    /// Connect to a FormuNex server; no local containers.
    Remote,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(default)]
pub struct DesktopConfig {
    /// `None` until the first-run wizard has chosen a mode.
    pub mode: Option<Mode>,
    pub remote_url: String,
    pub api_port: u16,
    /// Backend image (without tag) and tag used by the local stack.
    pub image: String,
    pub version: String,
    /// Set once the wizard finished (stack started and an administrator exists, or the server answered).
    pub setup_complete: bool,
    /// Explicit path to the `docker` executable when it is not found automatically.
    pub docker_path: Option<String>,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        Self {
            mode: None,
            remote_url: String::new(),
            api_port: DEFAULT_API_PORT,
            image: DEFAULT_IMAGE.to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            setup_complete: false,
            docker_path: None,
        }
    }
}

impl DesktopConfig {
    /// Base URL the UI talks to, or `None` before a mode is chosen.
    pub fn api_base(&self) -> Option<String> {
        match self.mode? {
            Mode::Local => Some(format!("http://127.0.0.1:{}", self.api_port)),
            Mode::Remote => (!self.remote_url.is_empty()).then(|| self.remote_url.clone()),
        }
    }

    pub fn load(dir: &Path) -> Result<Self> {
        let path = dir.join(CONFIG_FILE);
        if !path.exists() {
            return Ok(Self::default());
        }
        let text = fs::read_to_string(&path)?;
        serde_json::from_str(&text).map_err(|e| DesktopError::Config(format!("{}: {e}", path.display())))
    }

    pub fn save(&self, dir: &Path) -> Result<PathBuf> {
        fs::create_dir_all(dir)?;
        let path = dir.join(CONFIG_FILE);
        let tmp = dir.join(format!("{CONFIG_FILE}.tmp"));
        fs::write(&tmp, serde_json::to_vec_pretty(self).expect("config serialises"))?;
        fs::rename(&tmp, &path)?;
        Ok(path)
    }

    /// Apply a change from the UI, validating it first.
    pub fn validated(mut self) -> Result<Self> {
        if self.mode == Some(Mode::Remote) {
            self.remote_url = normalize_remote_url(&self.remote_url)?;
        }
        if self.api_port < 1024 {
            return Err(DesktopError::Config("本机 API 端口须在 1024–65535 之间".into()));
        }
        if self.image.trim().is_empty() || self.version.trim().is_empty() {
            return Err(DesktopError::Config("镜像与版本不能为空".into()));
        }
        if !valid_tag(&self.version) {
            return Err(DesktopError::Config(format!("镜像版本 “{}” 不合法", self.version)));
        }
        Ok(self)
    }
}

/// A remote server must use https (certificates are checked against the OS trust store, so an enterprise
/// CA installed there works); plain http is accepted only for this computer. Returns the URL without a
/// trailing slash, query or fragment.
pub fn normalize_remote_url(raw: &str) -> Result<String> {
    let parsed = url::Url::parse(raw.trim()).map_err(|_| DesktopError::Config(format!("无效的服务器地址：{raw}")))?;
    let host = parsed.host_str().unwrap_or("").to_ascii_lowercase();
    let loopback = matches!(host.as_str(), "localhost" | "127.0.0.1" | "[::1]" | "::1");
    match parsed.scheme() {
        "https" => {}
        "http" if loopback => {}
        "http" => return Err(DesktopError::Config("远程服务器必须使用 https（仅本机地址可用 http）".into())),
        other => return Err(DesktopError::Config(format!("不支持的协议：{other}"))),
    }
    if host.is_empty() {
        return Err(DesktopError::Config("服务器地址缺少主机名".into()));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(DesktopError::Config("服务器地址不能包含用户名或密码".into()));
    }
    let mut out = format!("{}://{}", parsed.scheme(), parsed.host_str().unwrap_or(""));
    if let Some(port) = parsed.port() {
        out.push_str(&format!(":{port}"));
    }
    let path = parsed.path().trim_end_matches('/');
    out.push_str(path);
    Ok(out)
}

/// Docker image tags: `[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`.
pub fn valid_tag(tag: &str) -> bool {
    let mut chars = tag.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphanumeric() || c == '_' => {}
        _ => return false,
    }
    tag.len() <= 128 && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_has_no_api_base_until_a_mode_is_chosen() {
        let mut c = DesktopConfig::default();
        assert_eq!(c.api_base(), None);
        c.mode = Some(Mode::Local);
        assert_eq!(c.api_base().as_deref(), Some("http://127.0.0.1:8000"));
        c.mode = Some(Mode::Remote);
        assert_eq!(c.api_base(), None);
        c.remote_url = "https://fx.example.com".into();
        assert_eq!(c.api_base().as_deref(), Some("https://fx.example.com"));
    }

    #[test]
    fn remote_url_rules() {
        assert_eq!(normalize_remote_url("https://fx.example.com/").unwrap(), "https://fx.example.com");
        assert_eq!(
            normalize_remote_url(" https://fx.example.com:8443/formunex/?x=1#a ").unwrap(),
            "https://fx.example.com:8443/formunex"
        );
        assert_eq!(normalize_remote_url("http://127.0.0.1:8000").unwrap(), "http://127.0.0.1:8000");
        assert_eq!(normalize_remote_url("http://localhost:8000/").unwrap(), "http://localhost:8000");
        assert!(normalize_remote_url("http://fx.example.com").is_err());
        assert!(normalize_remote_url("ftp://fx.example.com").is_err());
        assert!(normalize_remote_url("https://user:pw@fx.example.com").is_err());
        assert!(normalize_remote_url("not a url").is_err());
    }

    #[test]
    fn tags() {
        for ok in ["latest", "0.2.0", "v1.2.3-rc.1", "sha_abc"] {
            assert!(valid_tag(ok), "{ok}");
        }
        for bad in ["", "-x", "a b", "a;rm", &"x".repeat(129)] {
            assert!(!valid_tag(bad), "{bad}");
        }
    }

    #[test]
    fn save_load_roundtrip_and_validation() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(DesktopConfig::load(dir.path()).unwrap(), DesktopConfig::default());
        let c = DesktopConfig {
            mode: Some(Mode::Remote),
            remote_url: "https://fx.example.com/".into(),
            ..Default::default()
        }
        .validated()
        .unwrap();
        assert_eq!(c.remote_url, "https://fx.example.com");
        c.save(dir.path()).unwrap();
        assert_eq!(DesktopConfig::load(dir.path()).unwrap(), c);
        let bad = DesktopConfig { api_port: 80, ..Default::default() };
        assert!(bad.validated().is_err());
        let bad = DesktopConfig { version: "1.0;x".into(), ..Default::default() };
        assert!(bad.validated().is_err());
    }

    #[test]
    fn unknown_fields_and_missing_fields_are_tolerated() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join(CONFIG_FILE), r#"{"mode":"local","future":1}"#).unwrap();
        let c = DesktopConfig::load(dir.path()).unwrap();
        assert_eq!(c.mode, Some(Mode::Local));
        assert_eq!(c.api_port, DEFAULT_API_PORT);
    }
}
