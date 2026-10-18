<#
.SYNOPSIS
  FormuNex 一键安装脚本 (Windows)

.DESCRIPTION
  两种模式：
    Docker 模式（默认，推荐）：PostgreSQL + Redis + Neo4j + 后端 + 前端 全部走 Docker。
      FormuNex 在所有部署形态下都以 PostgreSQL 为主库，SQLite 仅用于 CI/单测。
    Native 模式（-Native）：本地 venv + npm + SQLite，仅用于开发调试。

  Docker 模式的前置条件是 Linux 容器：Windows 容器模式的 daemon 拉不了 Linux
  镜像（compose pull 以 no matching manifest 失败）。检测到 Windows 容器模式时，
  能切到 Linux 容器则切（Docker Desktop 的 DockerCli.exe -SwitchLinuxEngine）；
  无法切换时警告并跳过 Docker 栈、改走 Native 安装（不交互，全取默认值），
  不让可选栈拖垮整个安装。

  用法:
    # 方式一（推荐）：双击 install.bat（Docker 模式）
    # 方式二：powershell -ExecutionPolicy Bypass -File install.ps1
    # Native 模式：      install.ps1 -Native
    # Native + 只装核心： install.ps1 -Native -Minimal
    # Native + torch 引擎：install.ps1 -Native -Full
    # Native + 跳过前端： install.ps1 -Native -SkipFrontend

  Docker 模式不需要 Python / Node.js，只需要 Docker Desktop。

  Native 模式的 Windows 专有处理 —— 以下四处是 POSIX 脚本照搬过来必然踩的：
  * 全程用 .venv\Scripts\python.exe 绝对路径调用，不碰 Activate.ps1。
  * venv 按解释器校验并可能重建。Linux 上建的 .venv 只有 bin/ 没有 Scripts\，
    Windows 上根本激活不了；换个 Python 小版本复用旧 venv 也会因 wheel 标签
    （cp311/cp312）不匹配而装不上。检测到这两种情况就把旧 venv 改名为
    .venv.bak-<时间戳> 再重建，不直接删。
  * 依赖分档安装。pip 把 -e ".[a,b,c]" 的全部 extras 放在同一个解析事务里，
    一个不可能满足的依赖会让整条命令失败、并且什么都不装。那个依赖是 ColBERT：
    ragatouille → voyager 的 Windows wheel 只到 CPython 3.12，3.13/3.14 上会显式
    跳过并提示（RAG 自动退回 BM25 / TF-IDF / sentence-transformers）。
  * Celery 在 Windows 必须 --pool=solo（默认 prefork 池无法运行），启动脚本里已固定。
#>
param(
  [switch]$Native,        # Native 模式：本地 venv + npm + SQLite（默认 Docker 模式）
  [switch]$Minimal,       # Native 模式：只装 requirements.txt + 后端核心，不装可选引擎
  [switch]$Full,          # Native 模式：追加 torch 引擎（bo / heavy / colbert，走 CPU wheel 源）
  [switch]$SkipFrontend   # Native 模式：跳过前端 npm install
)

$ErrorActionPreference = "Stop"
# Docker 模式回退到 Native 时置 $true：回退路径不交互，全部取默认值。
$script:NonInteractive = $false
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$BackendDir = Join-Path $Root "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Write-Step($msg) { Write-Host "" ; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg ✓" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    ⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    ❌ $msg" -ForegroundColor Red }

# 原生命令（pip / npm / alembic / docker）失败不会触发 $ErrorActionPreference = "Stop"：
# 不检查 $LASTEXITCODE 的话，依赖没装上也会一路打印「安装完成」。
function Invoke-Step($What, [scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE) {
    Write-Err "$What 失败（退出码 $LASTEXITCODE），已中止安装"
    exit $LASTEXITCODE
  }
}

# 可选步骤：成功返回 $true，失败只警告。
function Invoke-Optional([scriptblock]$Command) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command | Out-Host
    return (-not $LASTEXITCODE)
  } finally {
    $ErrorActionPreference = $previous
  }
}

# 探测型调用：要拿输出，又不能在失败时中止安装，所以返回而不是抛出。
function Invoke-Probe([scriptblock]$Command) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $Command 2>&1 | Out-String
    return @{ Ok = (-not $LASTEXITCODE); Output = $output.Trim() }
  } catch {
    return @{ Ok = $false; Output = "$_" }
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Find-Python {
  # 返回 @{ Exe = <python.exe>; Version = "3.12" }，找不到返回 $null。
  $candidates = @()
  if (Get-Command "py" -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.13", "3.14")) {
      $candidates += @{ Exe = "py"; Args = @("-$v") }
    }
  }
  foreach ($name in @("python", "python3")) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { $candidates += @{ Exe = $c.Source; Args = @() } }
  }

  $code = "import sys;print('{0}.{1}'.format(*sys.version_info[:2]));print(sys.executable)"
  foreach ($cand in $candidates) {
    $probe = Invoke-Probe { & $cand.Exe @($cand.Args + @("-c", $code)) }
    if (-not $probe.Ok) { continue }
    $lines = @($probe.Output -split "\r?\n" | Where-Object { $_.Trim() })
    if ($lines.Count -lt 2) { continue }
    $version = $lines[0].Trim()
    $exe = $lines[1].Trim()
    if ($version -notmatch '^\d+\.\d+$') { continue }
    if ([version]$version -lt [version]"3.11") { continue }
    return @{ Exe = $exe; Version = $version }
  }
  return $null
}

# .env 配置（两种模式共用）
function Setup-Env {
  Write-Step "配置 .env"
  $envFile = Join-Path $Root ".env"
  if (Test-Path $envFile) {
    Write-Host "    .env 已存在，跳过"
  } else {
    Copy-Item (Join-Path $Root ".env.example") $envFile
    Write-Host "    已从 .env.example 生成 .env"
    Write-Host "    内网/免登录：FORMUNEX_API_AUTH_ENABLED=false"
    Write-Host "    LLM Key 可选（不填则用规则引擎离线运行）"
  }
}

# Docker Desktop 的容器模式切换。GH Windows runner 用的是 Windows 容器模式的
# dockerd（无 Docker Desktop），DockerCli.exe 不存在时无法切换，返回 $false。
function Switch-DockerToLinux {
  $cli = Join-Path $env:ProgramFiles "Docker\Docker\DockerCli.exe"
  if (-not (Test-Path $cli)) {
    Write-Host "    未找到 Docker Desktop（DockerCli.exe），无法切换容器模式"
    return $false
  }
  Write-Host "    正在切换到 Linux 容器（DockerCli.exe -SwitchLinuxEngine）..."
  $sw = Invoke-Probe { & $cli -SwitchLinuxEngine }
  if (-not $sw.Ok) {
    Write-Warn "切换到 Linux 容器失败：$($sw.Output)"
    return $false
  }
  for ($i = 1; $i -le 30; $i++) {
    $probe = Invoke-Probe { & docker info --format "{{.OSType}}" }
    if ($probe.Ok -and $probe.Output -eq "linux") { return $true }
    Start-Sleep -Seconds 2
  }
  Write-Warn "切换后 60 秒内 Docker 仍未回到 Linux 容器模式"
  return $false
}

# ---------------------------------------------------------------------------
# Docker 模式（默认，推荐）
# ---------------------------------------------------------------------------
function Install-Docker {
  Write-Step "[1/4] 环境检查（Docker 模式）"

  $dockerCmd = Get-Command "docker" -ErrorAction SilentlyContinue
  if (-not $dockerCmd) {
    Write-Err "未找到 docker"
    Write-Host "  请安装 Docker Desktop：https://www.docker.com/products/docker-desktop/"
    Write-Host "  或：winget install Docker.DockerDesktop"
    exit 1
  }
  $dockerVer = (Invoke-Probe { & docker --version }).Output
  Write-Ok "Docker $dockerVer"
  # 3-12：Docker Desktop 许可告知（用户必经的安装流程）。
  Write-Host "  提示：使用 Docker Desktop 需遵守 Docker Desktop 许可条款，详见 Docker 官网法律条款页（docker.com/legal）。"

  $composeProbe = Invoke-Probe { & docker compose version }
  if (-not $composeProbe.Ok) {
    Write-Err "未找到 docker compose v2（需要 Docker Compose v2，docker-compose v1 不可用）"
    Write-Host "  Docker Desktop 自带 v2；请更新 Docker Desktop"
    exit 1
  }
  Write-Ok "Docker Compose $($composeProbe.Output)"

  $infoProbe = Invoke-Probe { & docker info }
  if (-not $infoProbe.Ok) {
    Write-Err "Docker daemon 未运行"
    Write-Host "  请启动 Docker Desktop 后重试"
    exit 1
  }
  Write-Ok "Docker daemon 运行中"

  # 容器模式检测：Windows 容器模式的 daemon 拉不了 Linux 镜像，compose pull 会以
  # no matching manifest 失败。能切到 Linux 容器则切；不能则跳过 Docker 栈、
  # 改走 Native 安装（venv + SQLite），不要让可选栈拖垮整个安装。
  $osType = (Invoke-Probe { & docker info --format "{{.OSType}}" }).Output
  if ($osType -eq "windows") {
    Write-Warn "Docker 当前为 Windows 容器模式，无法运行 Linux 镜像"
    if (-not (Switch-DockerToLinux)) {
      Write-Warn "跳过 Docker 栈（PostgreSQL / Redis / Neo4j），改为 Native 安装"
      Write-Host "    如需 Docker 模式：打开 Docker Desktop → 切换到 Linux 容器，或执行 DockerCli.exe -SwitchLinuxEngine 后重跑安装"
      $script:NonInteractive = $true
      Install-Native
      return
    }
    Write-Ok "已切换到 Linux 容器模式"
  } elseif ($osType -ne "linux") {
    Write-Warn "无法确定 Docker 容器模式（docker info 返回 '$osType'），继续按 Linux 模式尝试"
  }

  Setup-Env

  Write-Step "[2/4] 拉取镜像并启动服务"
  Write-Host "    包含：PostgreSQL 16 + Redis 7 + Neo4j 5 + 后端 API + Celery worker + 前端"
  Write-Host "    首次拉取约 1-2GB，取决于网络速度"
  Push-Location $Root
  try {
    Invoke-Step "拉取镜像" { & docker compose pull postgres redis kg backend worker beat frontend }
    Invoke-Step "启动数据库" { & docker compose up -d postgres redis kg }
  } finally { Pop-Location }
  Write-Host "    等待数据库就绪..."
  $ready = $false
  for ($i = 1; $i -le 30; $i++) {
    $pgProbe = Invoke-Probe { & docker compose exec -T postgres pg_isready -U formunex -d formunex }
    if ($pgProbe.Ok) { $ready = $true; break }
    Start-Sleep -Seconds 1
  }
  if (-not $ready) {
    Write-Err "PostgreSQL 30 秒内未就绪，请检查 docker compose logs postgres"
    exit 1
  }
  Write-Ok "PostgreSQL 就绪"

  Write-Step "[3/4] 数据库迁移"
  Push-Location $Root
  try {
    Invoke-Step "数据库迁移" { & docker compose run --rm backend alembic upgrade head }
  } finally { Pop-Location }
  Write-Host "    数据库已就绪"

  Write-Step "[4/4] 启动全部服务"
  Push-Location $Root
  try {
    Invoke-Step "启动服务" { & docker compose up -d backend worker beat frontend }
  } finally { Pop-Location }
  Write-Host "    等待后端健康检查..."
  $healthy = $false
  for ($i = 1; $i -le 30; $i++) {
    try {
      $resp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
      if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
  }
  if ($healthy) { Write-Ok "后端健康检查通过" }
  else { Write-Warn "后端 30 秒内未就绪，请检查 docker compose logs backend" }

  Write-Host ""
  Write-Host "✅ 安装完成" -ForegroundColor Green
  Write-Host ""
  Write-Host "  前端:    http://localhost:5173"
  Write-Host "  后端 API: http://localhost:8000"
  Write-Host "  健康检查: curl http://localhost:8000/health"
  Write-Host "  查看日志: docker compose logs -f [backend|worker|frontend]"
  Write-Host "  停止服务: docker compose down"
  Write-Host "  停止并清数据: docker compose down -v  （⚠️ 会删除 PostgreSQL 数据）"
  Write-Host ""
  Write-Host "  首次使用：浏览器打开 http://localhost:5173，按向导创建管理员账号"
}

# ---------------------------------------------------------------------------
# Native 模式（开发调试）
# ---------------------------------------------------------------------------
function Install-Native {
  # 可选引擎的档位。分档的原因见文件头：一个不可满足的 extra 会让整条 pip 命令失败。
  $extras = @(
    "science", "optimize", "intel", "file_ingest", "report_export", "parse_pro",
    "export", "embedding", "pydoe", "baybe", "color", "crag", "notebooklm",
    "dev", "postgres"
  )
  $torchExtras = @("bo", "heavy")

  Write-Step "Native 1/5 环境检查"

  $py = Find-Python
  if (-not $py) {
    Write-Err "未找到 Python 3.11+（3.10 及更低装不上 requirements.txt：numpy 2.x 要求 >= 3.11）"
    Write-Host "  请先安装 Python 3.12（推荐，ColBERT 需要 ≤3.12）："
    Write-Host "    winget install --id Python.Python.3.12"
    Write-Host "    或 https://www.python.org/downloads/windows/"
    exit 1
  }
  $pyMinor = [int]($py.Version.Split(".")[1])
  $colbertOk = ($pyMinor -le 12)
  Write-Ok "Python $($py.Version) ($($py.Exe))"
  if (-not $colbertOk) {
    Write-Warn "CPython $($py.Version) 没有 ColBERT 的 Windows wheel（ragatouille → voyager 到 3.12 为止）"
    Write-Host "        这一档会被跳过，其余全部安装；RAG 自动退回 BM25 / TF-IDF / 向量检索"
  }
  if ([version]$py.Version -ge [version]"3.14") {
    Write-Warn "Python $($py.Version) 比本项目测试过的版本（3.11–3.13）新；科学依赖可能没有预编译包。"
  }

  $nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
  $npmCmd  = Get-Command "npm" -ErrorAction SilentlyContinue
  if ($nodeCmd) {
    $nodeProbe = Invoke-Probe { & $nodeCmd.Source --version }
    $nodeVer = ($nodeProbe.Output -replace "^v", "").Trim()
    if ($nodeVer -and ([version]$nodeVer -ge [version]"20.0")) { Write-Ok "Node.js v$nodeVer" }
    else { Write-Warn "Node.js v$nodeVer 过低（需要 >= 20），前端安装可能失败" }
    if (-not $npmCmd) { Write-Err "找到 node 但未找到 npm"; exit 1 }
  } else {
    Write-Warn "未找到 node — 将跳过前端安装（只跑 API 不受影响）"
    Write-Host "  winget install OpenJS.NodeJS.LTS  或 https://nodejs.org/"
  }

  if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Warn "未找到 git（不影响安装，推送代码时需要）"
  }

  # ---- [2/5] 后端依赖 ----
  Write-Step "Native 2/5 安装后端依赖（virtualenv + pinned requirements + extras）"

  $rebuildReason = ""
  if (Test-Path $VenvDir) {
    if (-not (Test-Path $VenvPython)) {
      $rebuildReason = "已有 .venv 里没有 Scripts\python.exe（Linux/macOS 上建的 venv，Windows 无法使用）"
    } else {
      $existing = Invoke-Probe { & $VenvPython -c "import sys;print('{0}.{1}'.format(*sys.version_info[:2]))" }
      $existingVersion = @($existing.Output -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -First 1)
      if ($existingVersion -and ($existingVersion.Trim() -ne $py.Version)) {
        $rebuildReason = "已有 .venv 是 CPython $($existingVersion.Trim())，与选中的 $($py.Version) 不一致（wheel 标签不匹配）"
      }
    }
  }

  if ($rebuildReason) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$VenvDir.bak-$stamp"
    Write-Warn "重建 .venv：$rebuildReason"
    Write-Host "    旧 venv 改名保留为 $backup"
    Rename-Item -Path $VenvDir -NewName (Split-Path -Leaf $backup)
  }

  if (-not (Test-Path $VenvPython)) {
    Write-Host "    创建虚拟环境 $VenvDir ..."
    Invoke-Step "创建虚拟环境" { & $py.Exe -m venv $VenvDir }
  } else {
    Write-Host "    复用已有虚拟环境"
  }
  Invoke-Step "升级 pip" { & $VenvPython -m pip install -U pip setuptools wheel }

  Push-Location $BackendDir
  try {
    Invoke-Step "安装 requirements.txt" { & $VenvPython -m pip install -r requirements.txt }
    Invoke-Step "安装后端（pip install -e .[dev,llm]）" { & $VenvPython -m pip install -e ".[dev,llm]" }

    if ($Minimal) {
      Write-Warn "跳过可选引擎（-Minimal）：DOE / 优化的原生引擎不需要它们"
    } else {
      $spec2 = ".[" + ($extras -join ",") + "]"
      Write-Host "    安装可选用引擎（无 torch）：$spec2"
      Invoke-Step "安装引擎 extras" { & $VenvPython -m pip install -e $spec2 }

      if ($Full) {
        $torch = @($torchExtras)
        if ($colbertOk) { $torch += "colbert" } else { Write-Warn "colbert 已跳过（CPython > 3.12）" }
        $spec3 = ".[" + ($torch -join ",") + "]"
        $cpuIndex = "https://download.pytorch.org/whl/cpu"
        Write-Host "    安装 torch 引擎：$spec3  --extra-index-url https://download.pytorch.org/whl/cpu"
        $torchOk = Invoke-Optional { & $VenvPython -m pip install -e $spec3 --extra-index-url $cpuIndex }
        if (-not $torchOk) {
          Write-Warn "torch 档安装失败（原生 DOE/优化器不依赖它，后端照常可用）"
        }
      } else {
        Write-Host "    未装 torch 引擎（bo/heavy/colbert）：需要时加 -Full 重跑"
      }
    }

    if (-not (Invoke-Optional { & $VenvPython -m pip install arxiv semanticscholar ddgs })) {
      Write-Warn "arxiv/semanticscholar/ddgs 安装失败，已跳过（在线检索可选）"
    }
  } finally { Pop-Location }

  $patchScript = Join-Path $BackendDir "scripts\apply_patches.py"
  if (Test-Path $patchScript) {
    if (-not (Invoke-Optional { & $VenvPython $patchScript })) {
      Write-Warn "补丁应用失败，详见 backend/scripts/reference/rapidocr-attribute-fix.md"
    }
  }

  # ---- [3/5] 前端依赖 ----
  Write-Step "Native 3/5 安装前端依赖"
  if ($SkipFrontend) {
    Write-Warn "已跳过（-SkipFrontend）"
  } elseif ($npmCmd) {
    Push-Location (Join-Path $Root "frontend")
    try { Invoke-Step "npm install (frontend)" { & npm install --legacy-peer-deps --no-audit --no-fund } } finally { Pop-Location }
  } else {
    Write-Warn "跳过：未找到 npm，稍后手动执行 cd frontend && npm install"
  }

  # ---- [4/5] .env 与数据库迁移 ----
  Setup-Env

  Write-Step "Native 5/5 数据库迁移（SQLite，仅开发用；生产请用 Docker 模式的 PostgreSQL）"
  Push-Location $BackendDir
  try { Invoke-Step "数据库迁移" { & $VenvPython -m alembic upgrade head } } finally { Pop-Location }
  Write-Host "    数据库已就绪"

  # ---- Embedding 模型选择 ----
  Write-Step "Embedding 模型选择"
  Write-Host "    选择向量模型（影响中英文检索质量）："
  Write-Host "    1) MiniLM-L6-v2 (默认, 90MB, 英文优先, 轻量)"
  Write-Host "    2) bge-small-zh-v1.5 (100MB, 中文)"
  Write-Host "    3) bge-m3 (推荐, ~2GB, 多语言, 解决跨语言召回)"
  Write-Host "    4) Qwen3-Embedding-0.6B (~1.2GB, 多语言)"
  Write-Host "    5) m3e-base (~200MB, 中文)"
  $embChoice = if ($script:NonInteractive) { "1" } else { Read-Host "    请选择 [1-5, 默认1]" }
  if ([string]::IsNullOrWhiteSpace($embChoice)) { $embChoice = "1" }
  switch ($embChoice) {
    "2" { $embModel = "BAAI/bge-small-zh-v1.5" }
    "3" { $embModel = "BAAI/bge-m3" }
    "4" { $embModel = "Qwen/Qwen3-Embedding-0.6B" }
    "5" { $embModel = "moka-ai/m3e-base" }
    default { $embModel = "sentence-transformers/all-MiniLM-L6-v2" }
  }
  $envFile = Join-Path $Root ".env"
  $envContent = Get-Content $envFile -Raw
  if ($envContent -match "^FORMUNEX_EMBEDDING_MODEL=") {
    $envContent = $envContent -replace "^FORMUNEX_EMBEDDING_MODEL=.*", "FORMUNEX_EMBEDDING_MODEL=$embModel"
    Set-Content $envFile $envContent -NoNewline
  } else {
    Add-Content $envFile "`nFORMUNEX_EMBEDDING_MODEL=$embModel"
  }
  Write-Host "    已选择: $embModel"
  if ($embModel -eq "BAAI/bge-m3" -or $embModel -eq "Qwen/Qwen3-Embedding-0.6B") {
    $dlAns = if ($script:NonInteractive) { "N" } else { Read-Host "    模型约 1-2GB，是否现在下载？[y/N]" }
    if ($dlAns -match "^[Yy]$") {
      Write-Host "    下载中..."
      $dlOk = Invoke-Optional { & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download('$embModel', allow_patterns=['*.json','*.txt','*.model','*.safetensors','*.bin']); print('    下载完成')" }
      if (-not $dlOk) { Write-Warn "模型下载失败，首次使用时将自动重试" }
    } else {
      Write-Host "    已跳过下载，首次使用时自动下载"
    }
  }

  Write-Host ""
  Write-Host "✅ 安装完成" -ForegroundColor Green
  Write-Host ""
  Write-Host "  一键启动（Redis/Neo4j 走 Docker，然后 API + worker + 前端）:"
  Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\windows\start-dev.ps1 start"
  Write-Host ""
  Write-Host "  手动启动（各开一个 PowerShell；不需要 activate，直接用 venv 里的解释器）:"
  Write-Host "    # 后端 API (:8000)"
  Write-Host "    cd $BackendDir; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload --reload-dir app"
  Write-Host ""
  Write-Host "    # Celery worker（Windows 必须 --pool=solo）"
  Write-Host "    cd $BackendDir; .\.venv\Scripts\python.exe -m celery -A app.worker.celery_app worker --pool=solo --loglevel=info"
  Write-Host ""
  Write-Host "    # 前端 (:5174)"
  Write-Host "    cd $(Join-Path $Root "frontend"); npm run dev"
  Write-Host ""
  Write-Host "  健康检查： curl http://localhost:8000/health"
  Write-Host ""
  Write-Host "  产品级运行（PostgreSQL + Redis + Neo4j）请用 Docker 模式："
  Write-Host "    .\install.bat"
  Write-Host "  详见 docs/QUICKSTART.md（中文：docs/快速入门.md）与 scripts/windows/README.md"
  Write-Host ""
}

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
# NOTE: 这一行是后端安装器测试的切片边界（Invoke-Probe → 第一个 Write-Step "[1/5]"）。
# 它必须在所有函数定义之后、顶层位置；函数内部不要再用 Write-Step "[N/5]" 格式，否则切片会在函数中途截断。
Write-Step "[1/5] 开始安装"
if ($Native) {
  Install-Native
} else {
  Install-Docker
}
