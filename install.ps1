# siacode-kit installer - run this once:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# It installs python deps and a `siacode` command in your PowerShell profile.

$ErrorActionPreference = "Stop"
$kit = $PSScriptRoot
Write-Host "== siacode-kit install ==" -ForegroundColor Cyan
Write-Host "kit folder: $kit"

# ---- 1. python check -------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "python not found - install Python 3.10+ (64-bit) from python.org" -ForegroundColor Red
    return
}
$ver = & python --version 2>&1
if ($ver -notmatch "Python 3\.") {
    Write-Host "need Python 3 (found: $ver)" -ForegroundColor Red
    return
}
Write-Host "python: $ver"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "claude (Claude Code CLI) not found in PATH - install it first:" -ForegroundColor Yellow
    Write-Host "  npm install -g @anthropic-ai/claude-code   (or your usual install method)"
}

# ---- 2. dependencies -------------------------------------------------------
Write-Host "installing python packages (requests, websockets, playwright)..." -ForegroundColor DarkGray
& python -m pip install -q -U requests websockets playwright
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed - check your python/pip" -ForegroundColor Red; return }

# ---- 3. siacode command into the profile -----------------------------------
$blockStart = "# >>> SIACODE-KIT >>>"
$blockEnd   = "# <<< SIACODE-KIT <<<"

$block = @'
# >>> SIACODE-KIT >>>
# siacode: your own Claude Code powered by MiniMax-M2.7 (230B-A10B, 196k ctx) on molab's
# free GPU. installed by siacode-kit. Kit folder: {{KIT}}
function siacode {
    $kit = "{{KIT}}"
    if (-not (Test-Path -LiteralPath "$kit\siacode.env")) {
        Write-Host "siacode.env missing - reinstall the kit in its folder" -ForegroundColor Red
        return
    }
    Write-Host "checking chain..." -ForegroundColor DarkGray
    Push-Location $kit
    try { python siacode_up.py --check } finally { }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "chain is down - auto-healing (this can take ~15 min)..." -ForegroundColor Yellow
        python siacode_up.py
        if ($LASTEXITCODE -ne 0) {
            Write-Host "siacode auto-heal FAILED - not starting. Debug: python siacode_up.py --check" -ForegroundColor Red
            Pop-Location; return
        }
    }
    $base = $null; $key = $null
    foreach ($line in (Get-Content -LiteralPath "$kit\siacode.env")) {
        if ($line -match '^MOLAB_BASE_URL\s*=\s*(.+)$') { $base = $Matches[1].Trim() }
        elseif ($line -match '^MOLAB_API_KEY\s*=\s*(.+)$') { $key = $Matches[1].Trim() }
    }
    if ([string]::IsNullOrWhiteSpace($base) -or [string]::IsNullOrWhiteSpace($key)) {
        Write-Host "siacode.env incomplete after healing - run: python link_molab.py" -ForegroundColor Red
        Pop-Location; return
    }
    $listening = $false
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', 8011)
        $listening = $c.Connected
        $c.Close()
    } catch { $listening = $false }
    if (-not $listening) {
        Write-Host "starting proxy on 127.0.0.1:8011 ..." -ForegroundColor DarkGray
        Start-Process -WindowStyle Hidden -FilePath python `
            -ArgumentList "$kit\molab_proxy.py", "--port", "8011" -WorkingDirectory $kit
        Start-Sleep -Seconds 3
    }
    $env:ANTHROPIC_BASE_URL         = "http://127.0.0.1:8011"
    $env:ANTHROPIC_AUTH_TOKEN       = $key
    $env:ANTHROPIC_MODEL            = "minimax-m2.7"
    $env:ANTHROPIC_SMALL_FAST_MODEL = "minimax-m2.7"
    $env:CLAUDE_CONFIG_DIR          = "$kit\.claude-home"
    try { & claude @args } finally {
        Remove-Item Env:ANTHROPIC_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_SMALL_FAST_MODEL -ErrorAction SilentlyContinue
        Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue
    }
}
# <<< SIACODE-KIT <<<
'@

$block = $block.Replace('{{KIT}}', $kit)

$profilePath = $PROFILE
if (-not (Test-Path $profilePath)) { New-Item -ItemType File -Force $profilePath | Out-Null }
$prof = Get-Content $profilePath -Raw -ErrorAction SilentlyContinue
if ($null -eq $prof) { $prof = "" }
# remove an old block if present, then append the new one (idempotent reinstall)
$s = $prof.IndexOf($blockStart); $e = $prof.IndexOf($blockEnd)
if ($s -ge 0 -and $e -gt $s) { $prof = $prof.Remove($s, $e - $s + $blockEnd.Length) }
$prof = $prof.TrimEnd() + "`r`n" + $block + "`r`n"
Set-Content -Path $profilePath -Value $prof -Encoding UTF8
Write-Host "installed: siacode command added to $profilePath" -ForegroundColor Green

# ---- 4. kit state files ----------------------------------------------------
if (-not (Test-Path "$kit\config.json")) { '{"ngrok_token": "", "notebook": ""}' | Set-Content "$kit\config.json" }
if (-not (Test-Path "$kit\siacode.env")) { "# filled automatically by siacode_up.py" | Set-Content "$kit\siacode.env" }
New-Item -ItemType Directory -Force "$kit\.claude-home" | Out-Null
New-Item -ItemType Directory -Force "$kit\proxy_log" | Out-Null

Write-Host ""
Write-Host "NEXT STEP (one-time, ~5 min):" -ForegroundColor Cyan
Write-Host "  cd '$kit'; python link_molab.py"
Write-Host "It asks for your (free) ngrok authtoken and connects your molab notebook."
Write-Host "After that, in any NEW PowerShell window just type:  siacode"