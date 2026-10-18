# FormuNex 反向代理与 TLS

## 原则：应用内不终止 TLS

FormuNex 应用本身**不做 TLS 终止**：

- 前端容器（nginx）只监听 HTTP（容器内 `:80`，宿主机映射 `5173`）；
- 后端（uvicorn）只监听 HTTP（`:8000`）；
- compose 默认把两者都只绑在 `127.0.0.1`，**不要**直接把 `5173` / `8000` 暴露到公网。

生产部署对外提供 HTTPS 时，在应用之外加一层反向代理，由**反代层终止 TLS**：

```
Internet --HTTPS--> 反代 nginx (:443，TLS 终止) --HTTP 127.0.0.1--> 前端容器 :5173 / 后端 :8000
```

这样证书只存在反代层，应用容器无需改动、也无需重启即可换证书。

## 启用步骤

### 1. 先以生产模式启动 FormuNex

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
curl -s http://127.0.0.1:8000/health   # 应返回 healthy
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/   # 应返回 200
```

生产模式下前端只绑 `127.0.0.1:5173`，后端只绑 `127.0.0.1:8000`。

### 2. 安装反代 nginx 并放入示例配置

```bash
# Debian/Ubuntu 示例；其他系统按各自包管理器安装 nginx
sudo apt install nginx

sudo cp deploy/reverse-proxy/nginx-tls.conf.example /etc/nginx/conf.d/formunex.conf
sudo nano /etc/nginx/conf.d/formunex.conf   # 替换全部占位符，见下一节
sudo nginx -t && sudo systemctl reload nginx
```

### 3. 替换占位符（必须改的三处）

| 占位符 | 改成什么 |
|---|---|
| `<你的域名>`（两处：`:80` 与 `:443` 的 `server_name`） | 你的真实域名，必须与证书域名一致 |
| `<证书 fullchain.pem 路径>` | 证书链文件路径 |
| `<证书 privkey.pem 路径>` | 私钥文件路径 |

其他按需改：若反代与 compose **不在同一台机器**，把 `upstream` 里的 `127.0.0.1` 改成 compose 所在主机的内网 IP，并把 compose 里前端/后端的绑定地址相应放开；如需 IPv6，取消 `listen [::]:443 ssl;` 的注释。

### 4. 用 Let's Encrypt 获取证书（推荐）

```bash
# 方式 A：certbot 自动配 nginx（会直接写 nginx 配置；之后对照示例补齐 SSE/WS 头）
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d <你的域名>

# 方式 B：只拿证书，手动填路径（推荐，与示例配置配合）
sudo apt install certbot
sudo certbot certonly --webroot -w /var/www/html -d <你的域名>
# 证书位于 /etc/letsencrypt/live/<你的域名>/fullchain.pem 与 privkey.pem
# 把这两个路径填入示例配置的 ssl_certificate / ssl_certificate_key

# 续期（证书有效期 90 天，建议进 cron 或 systemd timer）
sudo certbot renew --dry-run   # 先演练续期
```

### 5. 验证

```bash
curl -sI https://<你的域名>/            # 应返回 200，TLS 正常
curl -sI https://<你的域名>/health       # 应返回 200（后端健康检查经反代可达）
curl -sI http://<你的域名>/              # 应返回 301 跳转到 https
```

浏览器打开 `https://<你的域名>/`，应能正常登录使用；实时合规（WebSocket）与任务进度（SSE）应实时更新、无断流。

## 自签证书：仅供内网测试，不要用于生产

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout /etc/ssl/private/formunex-selfsigned.key \
  -out /etc/ssl/certs/formunex-selfsigned.crt -days 365 -subj "/CN=<你的域名>"
# 把上面两个路径填入示例配置的 ssl_certificate / ssl_certificate_key
```

浏览器会对自签证书报“不安全”，需手动信任后才能访问。内网测试可用，**生产请用 Let's Encrypt 或受信 CA 证书**。

## 常见故障

| 现象 | 排查 |
|---|---|
| 反代返回 502 | 先看 `docker compose ps`：前端/后端容器是否起来；再看 `docker compose logs frontend backend`；确认反代 `upstream` 地址与 compose 绑定地址一致 |
| 证书错误 / 浏览器报证书无效 | 检查证书路径、文件权限（nginx 需可读）、域名与证书域名是否一致 |
| WebSocket 连不上 / 实时合规无更新 | 检查 `/ws/` 段是否透传了 `Upgrade` 与 `Connection: upgrade` 头（示例配置已含） |
| 任务进度流卡住、过很久一次性爆发 | 检查 `/api/tasks/` 段是否 `proxy_buffering off`（示例配置已含） |
| 上传文件报 413 | 检查反代与前端容器的 `client_max_body_size` 是否为 `25m`（示例配置已含） |
| 启用 HSTS 后证书过期无法访问 | HSTS 会让浏览器在有效期内强制 HTTPS。确认 HTTPS 正常后再启用 `Strict-Transport-Security`；证书过期前及时续期 |

## 相关文档

- 生产部署总览：`docs/deployment.md`
- 桌面端连接服务器（只接受 https，本机地址除外）：`docs/desktop.md`
