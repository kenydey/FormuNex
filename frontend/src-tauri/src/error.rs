use serde::{Serialize, Serializer};

/// Errors returned to the UI as a plain (Chinese) message.
#[derive(Debug, thiserror::Error)]
pub enum DesktopError {
    #[error("{0}")]
    Config(String),
    #[error("未找到 Docker：请先安装并启动 Docker Desktop")]
    DockerMissing,
    #[error("Docker 命令失败（{command}）：{message}")]
    Docker { command: String, message: String },
    #[error("文件读写失败：{0}")]
    Io(#[from] std::io::Error),
    #[error("系统钥匙串不可用：{0}")]
    Keychain(String),
    #[error("{0}")]
    Http(String),
}

pub type Result<T> = std::result::Result<T, DesktopError>;

impl Serialize for DesktopError {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_string())
    }
}
