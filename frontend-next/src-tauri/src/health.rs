//! `/health` probe of the stack or remote server (start-up progress page, connection test).

use serde::Serialize;
use std::time::Duration;

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Health {
    pub reachable: bool,
    /// `ok` / `degraded` as reported by the server, `unreachable` otherwise.
    pub status: String,
    pub detail: Option<serde_json::Value>,
    pub error: Option<String>,
}

impl Health {
    pub fn down(error: impl Into<String>) -> Self {
        Self { reachable: false, status: "unreachable".into(), detail: None, error: Some(error.into()) }
    }
}

pub fn health_url(base: &str) -> String {
    format!("{}/health", base.trim_end_matches('/'))
}

/// Interpret a `/health` answer: anything that is not a JSON object with a `status` is not FormuNex.
pub fn interpret(http_status: u16, body: &str) -> Health {
    let value: serde_json::Value = serde_json::from_str(body).unwrap_or_default();
    match value.get("status").and_then(|s| s.as_str()) {
        Some(status) if (200..300).contains(&http_status) || http_status == 503 => {
            Health { reachable: true, status: status.to_string(), detail: Some(value.clone()), error: None }
        }
        _ => Health::down(format!("HTTP {http_status}：不是 FormuNex 服务（/health 未返回 status）")),
    }
}

pub async fn probe(base: &str) -> Health {
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(4)).build() {
        Ok(c) => c,
        Err(e) => return Health::down(e.to_string()),
    };
    match client.get(health_url(base)).send().await {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            interpret(code, &body)
        }
        Err(e) if e.is_timeout() => Health::down("连接超时"),
        Err(e) if e.is_connect() => Health::down("无法连接（服务未启动或地址错误）"),
        Err(e) => Health::down(e.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_and_interpretation() {
        assert_eq!(health_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000/health");
        let ok = interpret(200, r#"{"status":"ok","database":{"ok":true}}"#);
        assert!(ok.reachable);
        assert_eq!(ok.status, "ok");
        assert!(interpret(503, r#"{"status":"degraded"}"#).reachable);
        assert!(!interpret(200, "<html>").reachable);
        assert!(!interpret(404, r#"{"detail":"Not Found"}"#).reachable);
    }

    #[test]
    fn probe_reports_unreachable() {
        let h = tauri::async_runtime::block_on(probe("http://127.0.0.1:9"));
        assert!(!h.reachable);
        assert!(h.error.is_some());
    }
}
