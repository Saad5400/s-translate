# s-trans launcher for Windows (PowerShell 5+ / PowerShell 7+).
#   - installs `uv` if missing
#   - syncs Python deps
#   - warns about missing system binaries (LibreOffice, Tesseract, Ghostscript)
#   - builds the web UI if not already built and Node >=20 is available
#   - finds a free port if the default is taken
#   - starts the server and opens the browser
#
# Run from PowerShell:    .\run.ps1
# Or double-click run.cmd (which bypasses execution policy for you).

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Capture explicit overrides from the caller's environment so the .env loader
# below can't clobber them (e.g. `$env:HOST='0.0.0.0'; .\run.ps1`).
$overrideHost = $env:HOST
$overridePort = $env:PORT

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Bad($m)  { Write-Host $m -ForegroundColor Red }
function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Test-Port([int]$p) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', $p); $c.Close(); return $true
  } catch { return $false }
}

trap {
  Bad ""
  Bad "==> Launcher failed: $($_.Exception.Message)"
  Bad "    Common causes:"
  Bad "      - No internet access (uv/deps download)"
  Bad "      - Disk full or no write permission in $ScriptDir"
  Bad "      - Corporate proxy blocking pypi.org / astral.sh"
  Bad "      - Antivirus quarantining downloaded binaries"
  exit 1
}

Info "s-trans launcher (windows, $env:PROCESSOR_ARCHITECTURE)"

# --- uv ---------------------------------------------------------------------
if (-not (Test-Cmd uv)) {
  Warn "uv not found — installing ..."
  try {
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  } catch {
    Bad "uv install failed. Install manually from https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  }
  # Pull the freshly-installed binary onto this session's PATH.
  $candidatePaths = @(
    "$env:USERPROFILE\.local\bin",
    "$env:USERPROFILE\.cargo\bin"
  )
  foreach ($p in $candidatePaths) {
    if (Test-Path "$p\uv.exe") { $env:Path = "$p;$env:Path" }
  }
}
if (-not (Test-Cmd uv)) {
  Bad "uv install completed but 'uv' is still not on PATH."
  Bad "Open a new terminal and re-run, or add %USERPROFILE%\.local\bin to PATH."
  exit 1
}
Ok ("uv: " + (uv --version))

# --- system binaries (warn-only) -------------------------------------------
$missing = @()
function Check-Bin($bin, $label) {
  if (Test-Cmd $bin) {
    Ok "${label}: $((Get-Command $bin).Source)"
  } else {
    Warn "${label}: NOT FOUND ($bin)"
    $script:missing += $label
  }
}

# Common install locations
$lo = "$env:ProgramFiles\LibreOffice\program"
if ((-not (Test-Cmd soffice)) -and (Test-Path "$lo\soffice.exe")) {
  $env:Path = "$lo;$env:Path"
}
$tess = "$env:ProgramFiles\Tesseract-OCR"
if ((-not (Test-Cmd tesseract)) -and (Test-Path "$tess\tesseract.exe")) {
  $env:Path = "$tess;$env:Path"
}

Check-Bin soffice   "LibreOffice (soffice)"
Check-Bin tesseract "Tesseract OCR"
Check-Bin gswin64c  "Ghostscript"

if ($missing.Count -gt 0) {
  Warn ""
  Warn "Some optional system binaries are missing. The app will run, but"
  Warn "PDF OCR and .doc/.ppt conversion may fail. Install them with winget:"
  Write-Host "    winget install --id TheDocumentFoundation.LibreOffice -e"
  Write-Host "    winget install --id UB-Mannheim.TesseractOCR -e"
  Write-Host "    winget install --id ArtifexSoftware.GhostScript -e"
  Warn ""
}

# --- .env -------------------------------------------------------------------
if ((-not (Test-Path .env)) -and (Test-Path .env.example)) {
  Copy-Item .env.example .env
  Ok "Created .env from .env.example — edit it to add provider keys."
}

# Export every KEY=VALUE from .env into this process so LiteLLM can see
# provider keys (DEEPSEEK_API_KEY, OPENAI_API_KEY, ...). pydantic-settings
# populates the Settings model but doesn't push into the process env.
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
      $eq = $line.IndexOf('=')
      $k = $line.Substring(0, $eq).Trim()
      $v = $line.Substring($eq + 1).Trim().Trim('"').Trim("'")
      if ($k) { [Environment]::SetEnvironmentVariable($k, $v, 'Process') }
    }
  }
}

# Re-apply caller overrides on top of .env values.
$envHost = if ($overrideHost) { $overrideHost } elseif ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$envPort = if ($overridePort) { [int]$overridePort } elseif ($env:PORT) { [int]$env:PORT } else { 7860 }

# --- Python deps ------------------------------------------------------------
Info "Installing Python dependencies (uv sync) ..."
uv sync --quiet

# --- web UI -----------------------------------------------------------------
if (-not (Test-Path "app\web\dist\index.html")) {
  if (Test-Cmd npm) {
    $nodeMajor = 0
    try { $nodeMajor = [int]((node -p 'process.versions.node.split(".")[0]') 2>$null) } catch {}
    if ($nodeMajor -lt 20) {
      Warn "Node.js >=20 required to build the UI (found: $(if (Test-Cmd node) { node -v } else { 'none' }))."
      Warn "Skipping UI build — install Node 20+ and re-run if you need the web UI."
    } else {
      Info "Building web UI ..."
      Push-Location app\web
      npm install --silent
      npm run build --silent
      Pop-Location
    }
  } else {
    Warn "app\web\dist\index.html missing and npm is not installed."
    Warn "The API will still work but the web UI won't be served."
    Warn "Install Node.js >=20: winget install OpenJS.NodeJS.LTS"
  }
}

# --- pick a free port -------------------------------------------------------
if (Test-Port $envPort) {
  $orig = $envPort
  foreach ($cand in @(7861, 7862, 7863, 7870, 8000, 8080, 8888)) {
    if (-not (Test-Port $cand)) { $envPort = $cand; break }
  }
  if ($envPort -eq $orig) {
    Bad "Port $orig is in use and no fallback port is free. Set PORT=<n> and re-run."
    exit 1
  }
  Warn "Port $orig was in use — using $envPort instead."
}
$Url = "http://${envHost}:${envPort}"

# --- launch -----------------------------------------------------------------
$opener = Start-Job -ScriptBlock {
  param($u, $p)
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $c = New-Object Net.Sockets.TcpClient
      $c.Connect('127.0.0.1', [int]$p)
      $c.Close()
      Start-Process $u
      return
    } catch { Start-Sleep -Milliseconds 500 }
  }
} -ArgumentList $Url, $envPort

try {
  Ok "==> Starting s-trans on $Url  (Ctrl+C to stop)"
  $env:HOST = $envHost
  $env:PORT = "$envPort"
  uv run python -m app.main
} finally {
  if ($opener) { Remove-Job -Force -Job $opener -ErrorAction SilentlyContinue }
}
