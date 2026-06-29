/**
 * Builds the tiny self-contained Windows installer - a WINDOWLESS .vbs launcher.
 *
 * Why .vbs and not .cmd: double-clicking a .cmd/.bat ALWAYS makes Windows open a
 * console window before the first line of the script even runs - that is the
 * "terminal flash" users see. A .vbs is run by wscript.exe, which has NO console
 * at all, so there is ZERO flash. The launcher writes the PowerShell worker to
 * the centralized cache and starts it fully hidden (window style 0 +
 * -WindowStyle Hidden); the web app is the UI and shows live progress via the
 * local install beacon.
 *
 * To avoid duplicating logic, we still assemble the proven cmd/PowerShell
 * polyglot below (its PowerShell worker body is the tested, parse-verified
 * payload), then slice off everything up to the `#PSBEGIN` marker and embed ONLY
 * the PowerShell worker inside the VBScript host (see buildVbsHost). The marker
 * is searched for as the concatenation 'PSB'+'EGIN' so the literal token
 * `#PSBEGIN` appears EXACTLY ONCE in the whole file (the real marker).
 *
 * The PowerShell body:
 *   1. Reads the manifest directly from the GitHub repo (parts branch)
 *   2. Downloads all parts in parallel (runspace pool) with retry + checksum
 *      verification, straight from GitHub's API using an embedded read-only
 *      token (so it never touches the SSO-gated Vercel app at install time)
 *   3. Reassembles the parts into the bundle zip and verifies the full checksum
 *   4. Extracts it and launches the existing offline installer (install.cmd)
 *
 * DOWNLOAD ROBUSTNESS (why this is more than a plain Invoke-RestMethod):
 *   - GitHub's Contents API serves files >1 MB by returning a 302 redirect to
 *     a pre-signed storage URL. If the Authorization header is forwarded across
 *     that redirect, storage rejects it (400/403) - which previously looked
 *     like a "stuck" download because every part then exhausted its retries.
 *     We therefore disable auto-redirect, and on a 3xx we re-request the
 *     Location WITHOUT the Authorization header.
 *   - Progress is reported as each part COMPLETES (not in submission order),
 *     plus a periodic heartbeat, so the user always sees forward motion.
 *   - Every part is cached under the centralized workspace
 *     (%USERPROFILE%\\TestingToolkitWeb\\.cache\\downloads\\<ref>) and skipped
 *     on re-run if its checksum already matches (true resume).
 *   - A full, trace-level log is ALWAYS written to a documented, stable folder
 *     (%USERPROFILE%\\TestingToolkitWeb\\logs, with %TEMP% fallback). Each run gets
 *     a timestamped installer-<stamp>.log plus a stable installer-last.log that
 *     always points at the most recent run. The cmd bootstrap additionally
 *     writes installer-bootstrap.log BEFORE PowerShell starts, so even a failure
 *     to launch PowerShell (policy / antivirus) leaves a breadcrumb instead of a
 *     window that flashes and vanishes. Logging is set up on the very first line
 *     and guarded by a top-level trap, so nothing fails silently. Trace logging
 *     is ON by default; set TT_VERBOSE=0 only to quiet the on-console debug echo
 *     (the log file always gets full detail).
 *
 * `repo`, `ref`, and `token` are injected from the server at download time, so
 * the token never lives in the repo or the client source - only inside the
 * generated installer, which is itself only downloadable by authorized users
 * through the project's SSO.
 */
export function buildWindowsInstaller(
  repo: string,
  ref: string,
  token: string,
  fresh = false,
): string {
  // Escape single quotes for safe embedding in PowerShell single-quoted strings.
  const psRepo = repo.replace(/'/g, "''")
  const psRef = ref.replace(/'/g, "''")
  const psToken = token.replace(/'/g, "''")

  const cmdPolyglot = `@echo off
setlocal enabledelayedexpansion
title Testing Toolkit Agent - Installer

rem ====================================================================
rem Durable logging from the VERY FIRST line. We create a documented,
rem stable log folder and write the bootstrap (cmd) steps here BEFORE
rem PowerShell is even launched, so that a failure to start PowerShell
rem (policy, antivirus, missing runtime) still leaves a breadcrumb on
rem disk instead of a window that flashes and vanishes with no trace.
rem All installer logs live together in one place.
rem ====================================================================
set "TT_LOG_DIR=%USERPROFILE%\\TestingToolkitWeb\\logs"
mkdir "%TT_LOG_DIR%" >nul 2>&1
if not exist "%TT_LOG_DIR%" set "TT_LOG_DIR=%TEMP%\\TestingToolkitWeb\\logs"
mkdir "%TT_LOG_DIR%" >nul 2>&1
if not exist "%TT_LOG_DIR%" set "TT_LOG_DIR=%TEMP%"
set "TT_BOOT_LOG=%TT_LOG_DIR%\\installer-bootstrap.log"
call :tslog "================ Testing Toolkit installer launched ================"
call :tslog "log dir : %TT_LOG_DIR%"
call :tslog "user=%USERNAME%  host=%COMPUTERNAME%  os=%OS%  hidden=%TT_HIDDEN%"

rem Centralized scratch/cache dir: EVERYTHING install-related lives under the
rem single TestingToolkitWeb root, not %TEMP%. Forwarded to the PS worker (which
rem uses it for the progress file and extraction scratch). Falls back to %TEMP%.
set "TT_CACHE_DIR=%USERPROFILE%\\TestingToolkitWeb\\.cache"
mkdir "%TT_CACHE_DIR%" >nul 2>&1
if not exist "%TT_CACHE_DIR%" set "TT_CACHE_DIR=%TEMP%"
call :tslog "cache dir : %TT_CACHE_DIR%"

set "_TT_PS1=%TT_CACHE_DIR%\\TestingToolkit_%RANDOM%%RANDOM%.ps1"
call :tslog "extracting PowerShell payload to %_TT_PS1%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $marker='#PS'+'BEGIN'; $c=[IO.File]::ReadAllText('%~f0'); $i=$c.IndexOf($marker); if ($i -lt 0) { throw 'PSBEGIN marker not found in installer' }; $start=$c.IndexOf([char]10, $i) + 1; [IO.File]::WriteAllText($env:_TT_PS1, $c.Substring($start), [Text.UTF8Encoding]::new($false)); exit 0 } catch { try { [IO.File]::AppendAllText($env:TT_BOOT_LOG, '[extract-error] ' + $_.Exception.Message + [Environment]::NewLine) } catch {}; exit 1 }"
set "_TT_EXTRACT=%ERRORLEVEL%"
if not "%_TT_EXTRACT%"=="0" goto :extract_failed
if not exist "%_TT_PS1%" goto :extract_failed
call :tslog "payload extracted OK"

rem Relaunch the worker hidden so there is no console window: the web app is
rem the UI and shows live progress via the beacon. We forward TT_LOG_DIR so the
rem PowerShell worker logs into the SAME folder. The hidden PowerShell deletes
rem its own temp .ps1 when it finishes (it must outlive this cmd).
if not "%TT_HIDDEN%"=="1" (
  set "TT_HIDDEN=1"
  call :tslog "launching hidden PowerShell worker"
  start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%_TT_PS1%"
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%_TT_PS1%"
set "_TT_CODE=%ERRORLEVEL%"
del "%_TT_PS1%" >nul 2>&1
call :tslog "worker exited with code %_TT_CODE%"
exit /b %_TT_CODE%

:extract_failed
call :tslog "[FATAL] could not extract the PowerShell payload (exit %_TT_EXTRACT%)."
echo.
echo   Testing Toolkit installer could not start.
echo.
echo   A diagnostic log was written to:
echo     %TT_BOOT_LOG%
echo.
echo   This is almost always PowerShell being blocked by Group Policy or
echo   antivirus. Please send the log file above to support.
echo.
pause
exit /b 1

:tslog
>>"%TT_BOOT_LOG%" echo [%DATE% %TIME%] %~1
exit /b 0
#PSBEGIN
$ProgressPreference = 'SilentlyContinue'

# === Durable TRACE logging - set up BEFORE anything that can fail =========
# This MUST come first. Earlier versions enabled ErrorActionPreference=Stop and
# referenced [Net.SecurityProtocolType]::Tls13 (undefined on some .NET builds)
# before any log existed, so an early failure killed the worker silently - a
# window that flashed and vanished with no log. We now open a trace log in a
# documented, stable folder FIRST, define a crash trap, and only THEN make
# errors terminating. The folder is shared with the offline installer and the
# agent so every log lives in one centralized place: the single
# TestingToolkitWeb workspace (%USERPROFILE%\\TestingToolkitWeb\\logs).
$LogDir = $env:TT_LOG_DIR
if (-not $LogDir) { $LogDir = Join-Path $env:USERPROFILE 'TestingToolkitWeb\\logs' }
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null }
catch {
  $LogDir = Join-Path $env:TEMP 'TestingToolkitWeb\\logs'
  try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch { $LogDir = $env:TEMP }
}
$stamp   = (Get-Date -Format 'yyyyMMdd-HHmmss')
$LogFile = Join-Path $LogDir ('installer-' + $stamp + '.log')
# A STABLE filename that always points at the most recent run so the user (and
# support) never has to hunt for a timestamped file.
$LastLog = Join-Path $LogDir 'installer-last.log'

# Centralized scratch/cache dir: the progress file + extraction scratch live
# under the single TestingToolkitWeb root, NOT %TEMP%. The cmd forwards
# TT_CACHE_DIR; fall back to it / USERPROFILE / TEMP defensively so nothing
# install-related is ever scattered outside the workspace.
$CacheDir = $env:TT_CACHE_DIR
if (-not $CacheDir) { $CacheDir = Join-Path $env:USERPROFILE 'TestingToolkitWeb\\.cache' }
try { New-Item -ItemType Directory -Force -Path $CacheDir -ErrorAction Stop | Out-Null }
catch { $CacheDir = $env:TEMP }

$global:TtLogWriter = $null
try {
  $global:TtLogWriter = [IO.StreamWriter]::new($LogFile, $false, (New-Object System.Text.UTF8Encoding($false)))
  $global:TtLogWriter.AutoFlush = $true
} catch {}

function Trace($level, $msg) {
  $line = ('{0}  [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $level, $msg)
  if ($global:TtLogWriter) { try { $global:TtLogWriter.WriteLine($line) } catch {} }
}
# Trace-level logging is ON by default (operator asked for verbose traces). The
# file ALWAYS gets full detail; set TT_VERBOSE=0 only to quiet the on-console
# debug lines (irrelevant while hidden, useful when run with a console).
$Verbose = -not ($env:TT_VERBOSE -eq '0')

function Write-Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan; Trace 'STEP' $m }
function Write-Dbg($m)  { Trace 'TRACE' $m; if ($Verbose) { Write-Host ("    [debug] " + $m) -ForegroundColor DarkGray } }

Trace 'INFO' '================ installer worker started ================'
Trace 'INFO' ('log file : ' + $LogFile)
Trace 'INFO' ('hidden   : ' + ($env:TT_HIDDEN -eq '1'))
try { Trace 'INFO' ('PowerShell ' + $PSVersionTable.PSVersion.ToString() + ' on ' + [System.Environment]::OSVersion.VersionString) } catch {}

# Top-level trap: NOTHING fails silently anymore. Any unhandled terminating
# error is written (with position + script stack) to the trace log before the
# script unwinds, and a stable copy of the log is kept for support.
trap {
  Trace 'FATAL' ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
  try { Trace 'FATAL' ('at ' + $_.InvocationInfo.PositionMessage) } catch {}
  try { Trace 'FATAL' ('stack:' + [Environment]::NewLine + $_.ScriptStackTrace) } catch {}
  try { if ($global:TtLogWriter) { $global:TtLogWriter.Flush() } } catch {}
  try { Copy-Item -LiteralPath $LogFile -Destination $LastLog -Force -ErrorAction SilentlyContinue } catch {}
  try { Set-TtProgress 'error' ('Installer crashed: ' + $_.Exception.Message) } catch {}
  continue
}

# TLS: enable the strongest protocols this runtime ACTUALLY supports. Older
# Windows PowerShell / .NET builds do not define the Tls13 enum member, and
# merely referencing it throws - which (under ErrorActionPreference=Stop) is
# exactly what used to kill the installer before any log existed. Build the
# value defensively: start from Tls12 and OR in newer protocols only if defined.
try {
  $proto = [Net.SecurityProtocolType]::Tls12
  foreach ($name in @('Tls13')) {
    if ([Enum]::IsDefined([Net.SecurityProtocolType], $name)) {
      $proto = $proto -bor ([Net.SecurityProtocolType]$name)
    }
  }
  [Net.ServicePointManager]::SecurityProtocol = $proto
  Trace 'TRACE' ('TLS protocols: ' + [Net.ServicePointManager]::SecurityProtocol)
} catch { Trace 'WARN' ('could not set TLS protocols (continuing): ' + $_.Exception.Message) }

# Only NOW is it safe to make errors terminating (we have logging + a trap).
$ErrorActionPreference = 'Stop'

$Repo  = '${psRepo}'
$Ref   = '${psRef}'
$Token = '${psToken}'
$ApiBase = 'https://api.github.com/repos/' + $Repo + '/contents/'
$Concurrency = 4
if ($env:TT_CONCURRENCY -match '^[1-9][0-9]*$') { $Concurrency = [int]$env:TT_CONCURRENCY }
$MaxRetries  = 6
# Fresh install: ignore any previously downloaded parts and pull everything
# again from scratch. Injected by the server for reinstall downloads.
$Fresh = ${fresh ? "$true" : "$false"}
# Start-Transcript is intentionally NOT used: it starts too late and is silently
# unavailable in some constrained runtimes. Our own StreamWriter trace log above
# captures everything from the first line instead.
$Transcribing = $false
Trace 'INFO' ('repo=' + $Repo + ' ref=' + $Ref + ' concurrency=' + $Concurrency + ' fresh=' + $Fresh)
# A single in-place progress bar (overwrites itself with a leading CR) so the
# download shows clean forward motion instead of a wall of per-part lines.
function Show-Bar($done, $total) {
  $w = 28
  $frac = if ($total -gt 0) { $done / $total } else { 0 }
  $fill = [int][Math]::Round($frac * $w)
  if ($fill -gt $w) { $fill = $w }
  if ($fill -lt 0)  { $fill = 0 }
  $bar = ('#' * $fill) + ('-' * ($w - $fill))
  Write-Host -NoNewline ("\`r    [{0}] {1,3:N0}%  ({2}/{3})   " -f $bar, ($frac * 100), $done, $total)
}

# --- Install progress beacon ---------------------------------------------
# A tiny HTTP server on the agent port (127.0.0.1:7842) reports install
# progress to the web app BEFORE the real agent exists. This bootstrap writes
# its download progress to a shared file; the offline install.py writes the
# clean/install/copy/start phases to the SAME file (TT_INSTALL_PROGRESS). The
# beacon serves it at /install/progress and answers /health with 503
# "installing" so the app never mistakes the beacon for a live agent. It frees
# the port the moment install.py signals release_port (just before it starts
# the agent). Written without an HttpListener so no URL-ACL/admin is needed.
$ProgressPath = Join-Path $CacheDir 'install-progress.json'

function Set-TtProgress($phase, $message, $percent) {
  try {
    $o = [ordered]@{
      phase   = $phase
      message = $message
      ts      = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    }
    if ($null -ne $percent) { $o['percent'] = [int][Math]::Round([double]$percent) }
    $json = ($o | ConvertTo-Json -Compress)
    [IO.File]::WriteAllText($ProgressPath, $json, (New-Object System.Text.UTF8Encoding($false)))
  } catch {}
}

$beacon = {
  param($progressPath)
  $CRLF = [string][char]13 + [string][char]10
  function Read-Prog($p) {
    try { if (Test-Path -LiteralPath $p) { return [IO.File]::ReadAllText($p) } } catch {}
    return '{}'
  }
  function Test-StopFlag($p) {
    try {
      $t = (Read-Prog $p)
      if (-not $t) { return $false }
      $c = $t.Replace(' ', '')
      if ($c.Contains('"release_port":true')) { return $true }
      if ($c.Contains('"phase":"done"')) { return $true }
      if ($c.Contains('"phase":"error"')) { return $true }
    } catch {}
    return $false
  }
  $listener = $null
  while (-not (Test-StopFlag $progressPath)) {
    if ($null -eq $listener) {
      try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 7842)
        $listener.Start()
      } catch { $listener = $null; Start-Sleep -Milliseconds 500; continue }
    }
    try {
      if (-not $listener.Pending()) { Start-Sleep -Milliseconds 150; continue }
      $client = $listener.AcceptTcpClient()
      $client.ReceiveTimeout = 2000
      $stream = $client.GetStream()
      $buf = [byte[]]::new(2048)
      $read = 0
      try { $read = $stream.Read($buf, 0, $buf.Length) } catch {}
      $req = ''
      if ($read -gt 0) { $req = [Text.Encoding]::ASCII.GetString($buf, 0, $read) }
      $firstLine = $req
      $nl = $req.IndexOf([char]10)
      if ($nl -ge 0) { $firstLine = $req.Substring(0, $nl) }
      $tokens = $firstLine.Trim().Split(' ')
      $method = $tokens[0]
      $target = '/'
      if ($tokens.Length -ge 2) { $target = $tokens[1] }
      $qi = $target.IndexOf('?')
      $path = $target
      if ($qi -ge 0) { $path = $target.Substring(0, $qi) }
      $cors = 'Access-Control-Allow-Origin: *' + $CRLF + 'Access-Control-Allow-Methods: GET, OPTIONS' + $CRLF + 'Access-Control-Allow-Headers: *' + $CRLF + 'Cache-Control: no-store' + $CRLF
      $status = '404 Not Found'
      $ctype = ''
      $bodyStr = ''
      if ($method -eq 'OPTIONS') {
        $status = '204 No Content'
      } elseif ($path -eq '/install/progress') {
        $status = '200 OK'; $ctype = 'application/json'; $bodyStr = (Read-Prog $progressPath)
      } elseif ($path -eq '/health') {
        $status = '503 Service Unavailable'; $ctype = 'application/json'; $bodyStr = '{"status":"installing"}'
      }
      $bb = [Text.Encoding]::UTF8.GetBytes($bodyStr)
      $head = 'HTTP/1.1 ' + $status + $CRLF
      if ($ctype) { $head = $head + 'Content-Type: ' + $ctype + $CRLF }
      $head = $head + $cors + 'Content-Length: ' + [string]$bb.Length + $CRLF + 'Connection: close' + $CRLF + $CRLF
      $hb = [Text.Encoding]::ASCII.GetBytes($head)
      try {
        $stream.Write($hb, 0, $hb.Length)
        if ($bb.Length -gt 0) { $stream.Write($bb, 0, $bb.Length) }
        $stream.Flush()
      } catch {}
      try { $stream.Close() } catch {}
      try { $client.Close() } catch {}
    } catch {}
  }
  if ($listener) { try { $listener.Stop() } catch {} }
}

# Start the beacon in a background runspace (best-effort).
$beaconRs = $null; $beaconPs = $null
try {
  $beaconRs = [RunspaceFactory]::CreateRunspace()
  $beaconRs.Open()
  $beaconPs = [PowerShell]::Create()
  $beaconPs.Runspace = $beaconRs
  [void]$beaconPs.AddScript($beacon).AddArgument($ProgressPath)
  [void]$beaconPs.BeginInvoke()
} catch {}

try {
  Write-Host ""
  Write-Host "  Testing Toolkit - offline agent installer" -ForegroundColor White
  Write-Host "  -----------------------------------------"
  Write-Dbg ("repo=" + $Repo + " ref=" + $Ref + " concurrency=" + $Concurrency + " verbose=" + $Verbose)
  Write-Dbg ("PowerShell " + $PSVersionTable.PSVersion.ToString() + " on " + [System.Environment]::OSVersion.VersionString)
  Write-Dbg ("trace log: " + $LogFile)

  # GitHub API headers. 'application/vnd.github.raw' returns the file bytes
  # directly. The token is read-only and scoped to this single repo.
  $headers = @{
    'Authorization' = 'Bearer ' + $Token
    'Accept'        = 'application/vnd.github.raw'
    'User-Agent'    = 'TestingToolkit-Installer'
  }

  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
  # Transient extraction scratch under the centralized cache (deleted on finish).
  $work = Join-Path $CacheDir ('work\\' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  # Stable cache keyed by ref => completed parts survive a re-run (true resume).
  # Kept inside the single centralized workspace so EVERYTHING (downloads, logs,
  # install, data) lives under %USERPROFILE%\\TestingToolkitWeb. Falls back to
  # %TEMP% only if that folder cannot be created.
  $wsRoot = $env:TT_WORKSPACE_DIR
  if (-not $wsRoot) { $wsRoot = Join-Path $env:USERPROFILE 'TestingToolkitWeb' }
  $cacheRoot = Join-Path $wsRoot '.cache\\downloads'
  try { New-Item -ItemType Directory -Force -Path $cacheRoot -ErrorAction Stop | Out-Null }
  catch { $cacheRoot = Join-Path $env:TEMP 'TestingToolkitWeb-cache'; Write-Dbg ('cache fell back to TEMP: ' + $cacheRoot) }
  # On a fresh (reinstall) download, wipe any previously downloaded parts so
  # nothing stale is reused and the whole bundle is fetched again.
  if ($Fresh) {
    Write-Host "  Fresh reinstall: clearing any previously downloaded parts." -ForegroundColor Yellow
    Write-Dbg ("purging cache " + $cacheRoot)
    try { Remove-Item -Recurse -Force -LiteralPath $cacheRoot -ErrorAction SilentlyContinue } catch {}
  }
  $partsDir = Join-Path $cacheRoot ($Ref -replace '[^A-Za-z0-9_.-]', '_')
  New-Item -ItemType Directory -Force -Path $partsDir | Out-Null
  Write-Dbg ("work dir:  " + $work)
  Write-Dbg ("parts cache: " + $partsDir)

  Write-Step "Reading bundle manifest"
  $manifestUrl = $ApiBase + 'manifest.json?ref=' + $Ref
  Write-Dbg ("GET " + $manifestUrl)
  $manifest = Invoke-RestMethod -Uri $manifestUrl -Headers $headers -UseBasicParsing
  $parts = @($manifest.parts)
  Write-Host ("    {0} parts" -f $manifest.partCount)
  if ($Verbose) {
    foreach ($p in $parts) {
      Write-Dbg ("part " + $p.name + "  sha256=" + $p.sha256.Substring(0, 12) + "...")
    }
  }

  Write-Step "Downloading agent bundle"

  # The download worker. Returns a structured result object (never throws) so
  # the main thread can log rich per-part diagnostics in real time.
  $worker = {
    param($name, $url, $dest, $token, $sha, $maxRetries)
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    Add-Type -AssemblyName System.Net.Http
    $log = New-Object System.Collections.Generic.List[string]
    function LL($m) { [void]$log.Add(((Get-Date).ToString('HH:mm:ss.fff') + '  ' + $m)) }
    $shaLower = $sha.ToLower()

    # Resume: a previously downloaded, checksum-valid part is reused as-is.
    if (Test-Path $dest) {
      try {
        $h0 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLower()
        if ($h0 -eq $shaLower) {
          LL 'cached copy valid; skipping download'
          return [pscustomobject]@{ Name = $name; Status = 'cached'; Bytes = (Get-Item $dest).Length; Attempts = 0; Ms = 0; Redirect = $false; Log = $log }
        }
        LL 'stale cached copy; re-downloading'
        Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
      } catch {}
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    for ($try = 1; $try -le $maxRetries; $try++) {
      $client = $null; $client2 = $null; $resp = $null; $fs = $null; $stream = $null
      $usedRedirect = $false
      try {
        $handler = New-Object System.Net.Http.HttpClientHandler
        # Do NOT auto-follow: GitHub redirects >1 MB blobs to storage and the
        # Authorization header must be dropped before we follow.
        $handler.AllowAutoRedirect = $false
        try {
          $handler.UseProxy = $true
          $handler.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
          $handler.Proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
          $px = $handler.Proxy.GetProxy([Uri]$url)
          if ($px -and $px.AbsoluteUri -ne ([Uri]$url).AbsoluteUri) { LL ('proxy: ' + $px.AbsoluteUri) } else { LL 'proxy: direct' }
        } catch { LL ('proxy detect failed: ' + $_.Exception.Message) }

        $client = New-Object System.Net.Http.HttpClient($handler)
        $client.Timeout = [TimeSpan]::FromMinutes(15)
        [void]$client.DefaultRequestHeaders.TryAddWithoutValidation('Authorization', 'Bearer ' + $token)
        [void]$client.DefaultRequestHeaders.TryAddWithoutValidation('Accept', 'application/vnd.github.raw')
        [void]$client.DefaultRequestHeaders.TryAddWithoutValidation('User-Agent', 'TestingToolkit-Installer')

        LL ('attempt ' + $try + ': GET ' + $url)
        $resp = $client.GetAsync($url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $code = [int]$resp.StatusCode
        LL ('status ' + $code)

        if ($code -ge 300 -and $code -lt 400) {
          $loc = $resp.Headers.Location
          if (-not $loc) { throw ('redirect (HTTP ' + $code + ') with no Location header') }
          $usedRedirect = $true
          LL ('redirect -> ' + $loc.Host + '  (dropping Authorization header)')
          $resp.Dispose(); $client.Dispose(); $resp = $null; $client = $null
          $h2 = New-Object System.Net.Http.HttpClientHandler
          $h2.AllowAutoRedirect = $true
          try {
            $h2.UseProxy = $true
            $h2.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
            $h2.Proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
          } catch {}
          $client2 = New-Object System.Net.Http.HttpClient($h2)
          $client2.Timeout = [TimeSpan]::FromMinutes(15)
          [void]$client2.DefaultRequestHeaders.TryAddWithoutValidation('User-Agent', 'TestingToolkit-Installer')
          $resp = $client2.GetAsync($loc, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
          LL ('redirected status ' + [int]$resp.StatusCode)
        }

        if (-not $resp.IsSuccessStatusCode) { throw ('HTTP ' + [int]$resp.StatusCode) }
        $len = $resp.Content.Headers.ContentLength
        if ($len) { LL ('content-length ' + [int]($len / 1KB) + ' KB') }

        $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $fs = [IO.File]::Create($dest)
        $stream.CopyTo($fs, 1MB)
        $fs.Close(); $fs = $null
        $stream.Dispose(); $stream = $null
        $resp.Dispose(); $resp = $null
        if ($client2) { $client2.Dispose(); $client2 = $null }
        if ($client) { $client.Dispose(); $client = $null }

        $bytes = (Get-Item $dest).Length
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLower()
        if ($actual -ne $shaLower) { throw ('checksum mismatch (got ' + $actual.Substring(0, 12) + '..., expected ' + $shaLower.Substring(0, 12) + '...)') }
        $sw.Stop()
        LL ('OK ' + [int]($bytes / 1KB) + ' KB in ' + [int]$sw.Elapsed.TotalSeconds + 's')
        return [pscustomobject]@{ Name = $name; Status = 'ok'; Bytes = $bytes; Attempts = $try; Ms = $sw.Elapsed.TotalMilliseconds; Redirect = $usedRedirect; Log = $log }
      } catch {
        LL ('attempt ' + $try + ' failed: ' + $_.Exception.Message)
        if ($fs) { try { $fs.Close() } catch {} }
        if ($stream) { try { $stream.Dispose() } catch {} }
        if ($resp) { try { $resp.Dispose() } catch {} }
        if ($client2) { try { $client2.Dispose() } catch {} }
        if ($client) { try { $client.Dispose() } catch {} }
        if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
        if ($try -eq $maxRetries) {
          $sw.Stop()
          return [pscustomobject]@{ Name = $name; Status = 'failed'; Bytes = 0; Attempts = $try; Ms = $sw.Elapsed.TotalMilliseconds; Redirect = $usedRedirect; Error = $_.Exception.Message; Log = $log }
        }
        Start-Sleep -Seconds ([Math]::Min(30, [Math]::Pow(2, $try)))
      }
    }
  }

  $pool = [RunspaceFactory]::CreateRunspacePool(1, $Concurrency)
  $pool.Open()
  $jobs = @()
  foreach ($p in $parts) {
    $ps = [PowerShell]::Create()
    $ps.RunspacePool = $pool
    [void]$ps.AddScript($worker).
      AddArgument($p.name).
      AddArgument($ApiBase + $p.name + '?ref=' + $Ref).
      AddArgument((Join-Path $partsDir $p.name)).
      AddArgument($Token).
      AddArgument($p.sha256).
      AddArgument($MaxRetries)
    $jobs += [pscustomobject]@{ PS = $ps; Handle = $ps.BeginInvoke(); Name = $p.name }
  }

  # Drive a single progress bar as parts complete (any order). Per-attempt and
  # per-part detail still goes to the transcript log; the console stays clean.
  $pending = [System.Collections.ArrayList]::new()
  foreach ($j in $jobs) { [void]$pending.Add($j) }
  $done = 0
  $failures = @()
  $total = $jobs.Count
  Show-Bar 0 $total
  Set-TtProgress 'downloading' 'Downloading agent bundle' 5
  while ($pending.Count -gt 0) {
    for ($i = $pending.Count - 1; $i -ge 0; $i--) {
      $j = $pending[$i]
      if ($j.Handle.IsCompleted) {
        $r = $null
        try { $r = ($j.PS.EndInvoke($j.Handle) | Select-Object -Last 1) }
        catch { $r = [pscustomobject]@{ Name = $j.Name; Status = 'failed'; Error = $_.Exception.Message; Attempts = $MaxRetries; Bytes = 0; Redirect = $false; Log = $null } }
        finally { $j.PS.Dispose() }
        $pending.RemoveAt($i)
        # Flush this part's full per-attempt detail (proxy/redirect/retry/checksum)
        # into the trace log so download failures are fully diagnosable offline.
        Trace 'PART' ($r.Name + ' -> ' + $r.Status + ' (attempts=' + $r.Attempts + ', bytes=' + $r.Bytes + ', redirect=' + $r.Redirect + ')')
        if ($r.Error) { Trace 'PART' ($r.Name + ' error: ' + $r.Error) }
        if ($r.Log) { foreach ($ll in $r.Log) { Trace 'PART' ($r.Name + '  ' + $ll) } }
        if ($r.Status -eq 'failed') { $failures += $r } else { $done++ }
        $seen = $done + $failures.Count
        Show-Bar $seen $total
        # Map download completion onto the first ~55% of the overall bar; the
        # offline install.py owns the remainder.
        $pct = 5; if ($total -gt 0) { $pct = 5 + (55.0 * $seen / $total) }
        Set-TtProgress 'downloading' ("Downloading agent bundle ({0}/{1} parts)" -f $seen, $total) $pct
      }
    }
    if ($pending.Count -gt 0) { Start-Sleep -Milliseconds 200 }
  }
  Write-Host ""
  $pool.Close(); $pool.Dispose()
  if ($failures.Count -gt 0) {
    foreach ($f in $failures) { Write-Host ("    [x] " + $f.Name + " failed: " + $f.Error) -ForegroundColor Red }
    throw ([string]$failures.Count + ' part(s) failed to download. Completed parts are cached and will be skipped when you re-run this installer. See the log: ' + $LogFile)
  }

  Write-Step "Reassembling bundle"
  Set-TtProgress 'extracting' 'Reassembling bundle' 61
  $zip = Join-Path $work $manifest.archive
  $out = [IO.File]::Create($zip)
  foreach ($p in ($parts | Sort-Object name)) {
    $fs = [IO.File]::OpenRead((Join-Path $partsDir $p.name))
    $fs.CopyTo($out, 1MB)
    $fs.Close()
  }
  $out.Close()

  $full = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
  if ($full -ne $manifest.sha256.ToLower()) { throw 'Final archive checksum mismatch - download may be corrupt. Delete the cache folder and re-run.' }
  Write-Host "    archive verified"

  Write-Step "Extracting"
  Set-TtProgress 'extracting' 'Extracting files' 63
  $dest = Join-Path $scriptDir $manifest.extractTo
  if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue }
  Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue

  # --- Overlay the latest Python code on top of the bundle ----------------
  # The 470 MB bundle (wheels/runtime/models) changes rarely, but the agent
  # code + installer change often. Rather than re-pack the whole bundle for
  # every code fix, pull the current source from the repo and lay it over the
  # extracted files. Best-effort: if it fails we fall back to bundled code.
  Write-Step "Applying latest agent code"
  Set-TtProgress 'overlay' 'Applying latest agent code' 64
  try {
    $um = Invoke-RestMethod -Uri ($ApiBase + 'agent-update.json?ref=' + $Ref) -Headers $headers -UseBasicParsing
    $srcRef = $um.ref
    Write-Dbg ("overlay ref=" + $srcRef + " files=" + @($um.files).Count)
    $n = 0
    foreach ($f in $um.files) {
      $target = Join-Path (Join-Path $dest 'src') ($f.path -replace '/', '\\')
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Invoke-RestMethod -Uri $f.url -Headers $headers -UseBasicParsing -OutFile $target
      $n++
    }
    Invoke-RestMethod -Uri ($ApiBase + 'agent-bundle/install.py?ref=' + $srcRef) -Headers $headers -UseBasicParsing -OutFile (Join-Path $dest 'install.py')
    # Overlay the latest requirements.txt so newly-added deps install offline.
    if ($um.requirements -and $um.requirements.url) {
      Invoke-RestMethod -Uri $um.requirements.url -Headers $headers -UseBasicParsing -OutFile (Join-Path $dest 'requirements.txt')
    }
    # Drop any extra wheels into the extracted wheelhouse so offline pip finds them.
    if ($um.extraWheels) {
      $wh = Join-Path $dest 'wheelhouse'
      New-Item -ItemType Directory -Force -Path $wh | Out-Null
      foreach ($w in $um.extraWheels) {
        Invoke-RestMethod -Uri $w.url -Headers $headers -UseBasicParsing -OutFile (Join-Path $wh $w.name)
      }
    }
    Write-Host ("    updated {0} source files to the latest version" -f $n) -ForegroundColor Green
  } catch {
    Write-Host ("    (using bundled code; overlay skipped: " + $_.Exception.Message + ")") -ForegroundColor DarkGray
  }

  $installCmd = Join-Path $dest 'install.cmd'
  if (-not (Test-Path $installCmd)) { throw ('install.cmd not found in extracted bundle at ' + $dest) }

  Write-Step "Running offline installer"
  Write-Host "    (this part never touches the internet)"
  Set-TtProgress 'installing_deps' 'Starting offline install' 65
  # Hand the auto-update settings to install.py so the agent can fetch future
  # patches on its own. These are read by write_update_config() in install.py.
  $env:TT_UPDATE_TOKEN = $Token
  $env:TT_UPDATE_REPO  = $Repo
  $env:TT_UPDATE_REF   = $Ref
  # Share the progress file so install.py reports clean/install/copy/start into
  # the same beacon (its default path matches $ProgressPath anyway).
  $env:TT_INSTALL_PROGRESS = $ProgressPath
  Push-Location $dest
  & cmd /c ('"' + $installCmd + '"')
  $code = $LASTEXITCODE
  Pop-Location

  Write-Host ""
  if ($code -eq 0) {
    Write-Host "  Done. Testing Toolkit is installed." -ForegroundColor Green
    Trace 'INFO' 'offline installer finished successfully (exit 0)'
  } else {
    Write-Host ("  Installer exited with code " + $code) -ForegroundColor Yellow
    Trace 'WARN' ('offline installer exited with code ' + $code)
  }
} catch {
  $errMsg = $_.Exception.Message
  Trace 'ERROR' ('install failed: ' + $errMsg)
  try { Trace 'ERROR' ('at ' + $_.InvocationInfo.PositionMessage) } catch {}
  try { Trace 'ERROR' ('stack:' + [Environment]::NewLine + $_.ScriptStackTrace) } catch {}
  Set-TtProgress 'error' ("Install failed: " + $errMsg)
  Write-Host ""
  Write-Host ("  ERROR: " + $errMsg) -ForegroundColor Red
  Write-Host ("  Debug log: " + $LogFile) -ForegroundColor Yellow
  Write-Host "  Nothing was installed. You can safely re-run this installer (finished parts are cached)."
  # The worker runs hidden, so a failure would otherwise be invisible. Pop a
  # best-effort message box pointing at the trace log so the user is never left
  # staring at a window that flashed and vanished with nothing to go on.
  if ($env:TT_HIDDEN -eq '1') {
    try {
      Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
      [void][System.Windows.Forms.MessageBox]::Show(
        ("Testing Toolkit could not finish installing." + [Environment]::NewLine + [Environment]::NewLine +
         $errMsg + [Environment]::NewLine + [Environment]::NewLine +
         "A full diagnostic log was saved to:" + [Environment]::NewLine + $LastLog + [Environment]::NewLine + [Environment]::NewLine +
         "You can safely re-run the installer (finished downloads are cached)."),
        "Testing Toolkit Installer",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) 
    } catch {}
  }
} finally {
  # Stop the progress beacon (it usually exits on its own once install.py
  # signals release_port, but force it down in case of an early failure).
  try { if ($beaconPs) { $beaconPs.Stop() } } catch {}
  try { if ($beaconRs) { $beaconRs.Close() } } catch {}
  # Always keep a stable copy of this run's trace log at installer-last.log so
  # there is a single, predictable file to open / send to support.
  try { if ($global:TtLogWriter) { $global:TtLogWriter.Flush() } } catch {}
  try { Copy-Item -LiteralPath $LogFile -Destination $LastLog -Force -ErrorAction SilentlyContinue } catch {}
  try { if ($global:TtLogWriter) { $global:TtLogWriter.Dispose(); $global:TtLogWriter = $null } } catch {}
  # Only prompt when we actually have a visible console. When relaunched hidden
  # (TT_HIDDEN=1) the web app is the UI, so a Read-Host would hang invisibly.
  if (-not ($env:TT_HIDDEN -eq '1')) {
    Write-Host ""
    Read-Host "  Press Enter to close"
  } else {
    # Hidden run owns its temp .ps1 (the cmd already exited) so clean it up.
    try { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue } catch {}
  }
}
`

  // Ship ONLY the PowerShell worker, wrapped in a windowless VBScript host.
  // Slice off everything up to and including the #PSBEGIN marker line; what
  // remains is the proven, parse-verified PowerShell worker body.
  const marker = "#PS" + "BEGIN"
  const mi = cmdPolyglot.indexOf(marker)
  const psBody = cmdPolyglot.slice(cmdPolyglot.indexOf("\n", mi) + 1)
  return buildVbsHost(psBody)
}

/**
 * Wraps the PowerShell worker in a VBScript launcher so the installer runs with
 * ZERO console flash. Double-clicking a .vbs runs it under wscript.exe, which
 * has no console window. The launcher:
 *   1. Re-launches itself under wscript if it somehow started under cscript
 *      (console host), so there is never a window.
 *   2. Creates the centralized dirs (~/TestingToolkitWeb\\logs and \\.cache) and
 *      writes a bootstrap breadcrumb to installer-bootstrap.log.
 *   3. Writes the PowerShell worker to a .ps1 in the cache dir. It is written as
 *      UTF-16LE (FSO Unicode), which `powershell -File` reads natively.
 *   4. Forwards TT_LOG_DIR / TT_CACHE_DIR / TT_HIDDEN and starts PowerShell
 *      fully hidden (Run window style 0 + -WindowStyle Hidden), without waiting.
 *      The worker outlives the launcher and deletes its own .ps1 on completion.
 *
 * The PowerShell body is embedded as VBScript string literals (one WriteLine per
 * source line; the only escaping needed inside a VBScript "..." literal is "" for
 * a double quote). No %TEMP% scratch and no extra COM beyond FileSystemObject /
 * WScript.Shell, to minimize what corporate AV / policy can block.
 */
function buildVbsHost(psBody: string): string {
  const lines = psBody.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")
  // Drop a single trailing empty line so we do not append a spurious blank line.
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop()
  const payload = lines
    .map((l) => `  out.WriteLine "${l.replace(/"/g, '""')}"`)
    .join("\r\n")

  const vbs = [
    `' Testing Toolkit Agent - windowless installer launcher (VBScript).`,
    `' Run by wscript.exe => NO console window => ZERO terminal flash. This writes`,
    `' the PowerShell worker to the centralized cache and starts it fully hidden.`,
    `' The web app is the UI and shows live progress via the local install beacon.`,
    `Option Explicit`,
    ``,
    `' If launched under cscript (console host), relaunch under wscript (windowless)`,
    `' and exit, so a console can never appear.`,
    `If InStr(LCase(WScript.FullName), "cscript") > 0 Then`,
    `  CreateObject("WScript.Shell").Run "wscript.exe " & Chr(34) & WScript.ScriptFullName & Chr(34), 0, False`,
    `  WScript.Quit 0`,
    `End If`,
    ``,
    `Dim fso, sh, env, userProfile, root, cacheDir, logDir, bootLog, ps1, out, cmd`,
    `Set fso = CreateObject("Scripting.FileSystemObject")`,
    `Set sh = CreateObject("WScript.Shell")`,
    `Set env = sh.Environment("Process")`,
    ``,
    `userProfile = env("USERPROFILE")`,
    `If userProfile = "" Then userProfile = sh.ExpandEnvironmentStrings("%USERPROFILE%")`,
    ``,
    `' Centralized root: EVERYTHING install-related lives under ~/TestingToolkitWeb.`,
    `root = userProfile & "\\TestingToolkitWeb"`,
    `cacheDir = root & "\\.cache"`,
    `logDir = root & "\\logs"`,
    `EnsureDir root`,
    `EnsureDir cacheDir`,
    `EnsureDir logDir`,
    `' Fall back to TEMP only if the workspace dirs cannot be created.`,
    `If Not fso.FolderExists(cacheDir) Then cacheDir = env("TEMP")`,
    `If Not fso.FolderExists(logDir) Then logDir = env("TEMP")`,
    ``,
    `bootLog = logDir & "\\installer-bootstrap.log"`,
    `WriteLog bootLog, "============ Testing Toolkit installer launched (vbs, windowless) ============"`,
    `WriteLog bootLog, "log dir   : " & logDir`,
    `WriteLog bootLog, "cache dir : " & cacheDir`,
    ``,
    `' Write the PowerShell worker (UTF-16LE; powershell -File reads it natively).`,
    `ps1 = cacheDir & "\\TestingToolkit_" & Replace(fso.GetTempName(), ".tmp", "") & ".ps1"`,
    `On Error Resume Next`,
    `Set out = fso.CreateTextFile(ps1, True, True)`,
    `If Err.Number <> 0 Then`,
    `  WriteLog bootLog, "[FATAL] could not create worker .ps1: " & Err.Description`,
    `  WScript.Quit 1`,
    `End If`,
    `On Error Goto 0`,
    `WritePayload out`,
    `out.Close`,
    `WriteLog bootLog, "wrote PowerShell worker to " & ps1`,
    ``,
    `' Forward centralized dirs + hidden flag to the worker (inherited by the child).`,
    `env("TT_LOG_DIR") = logDir`,
    `env("TT_CACHE_DIR") = cacheDir`,
    `env("TT_HIDDEN") = "1"`,
    ``,
    `' Launch FULLY HIDDEN (window style 0) and do NOT wait. wscript exits`,
    `' immediately; the worker outlives it and removes its own .ps1 when done.`,
    `cmd = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & ps1 & Chr(34)`,
    `On Error Resume Next`,
    `sh.Run cmd, 0, False`,
    `If Err.Number <> 0 Then WriteLog bootLog, "[FATAL] could not launch PowerShell: " & Err.Description`,
    `On Error Goto 0`,
    `WriteLog bootLog, "launched hidden PowerShell worker"`,
    `WScript.Quit 0`,
    ``,
    `Sub EnsureDir(p)`,
    `  On Error Resume Next`,
    `  If Not fso.FolderExists(p) Then fso.CreateFolder(p)`,
    `  On Error Goto 0`,
    `End Sub`,
    ``,
    `Sub WriteLog(path, msg)`,
    `  On Error Resume Next`,
    `  Dim f`,
    `  Set f = fso.OpenTextFile(path, 8, True)`,
    `  f.WriteLine "[" & Now & "] " & msg`,
    `  f.Close`,
    `  On Error Goto 0`,
    `End Sub`,
    ``,
    `Sub WritePayload(out)`,
    payload,
    `End Sub`,
    ``,
  ].join("\r\n")

  return vbs
}

/**
 * Builds the macOS / Linux smart installer (a bash script).
 *
 * Mirrors the Windows installer: it locates a system Python 3 (required by the
 * agent anyway) and hands off to an embedded Python downloader that fetches the
 * bundle parts directly from the private GitHub repo with the injected
 * read-only token, verifies every checksum, reassembles + verifies the full
 * archive, extracts it, and runs the existing offline install.sh (which copies
 * the agent, installs wheels offline, registers login auto-start, and launches
 * the agent). No size assumptions, resumable, and safe to re-run.
 *
 * Same download robustness as the Windows installer: redirects from GitHub's
 * Contents API are followed WITHOUT the Authorization header (otherwise storage
 * rejects >1 MB blobs), completed parts are cached under the centralized
 * workspace (~/TestingToolkitWeb/.cache/downloads/<ref>) for true resume, every
 * part is logged in real time, and a full trace log is written to
 * ~/TestingToolkitWeb/logs. Trace logging is on by default; set TT_VERBOSE=0 to
 * quiet only the on-console echo.
 *
 * `repo`, `ref`, and `token` are injected server-side at download time, exactly
 * like the Windows installer, so the token never lives in the repo or client.
 */
export function buildUnixInstaller(
  repo: string,
  ref: string,
  token: string,
  fresh = false,
): string {
  // Escape single quotes for safe embedding in bash single-quoted strings.
  const shRepo = repo.replace(/'/g, "'\\''")
  const shRef = ref.replace(/'/g, "'\\''")
  const shToken = token.replace(/'/g, "'\\''")
  const shFresh = fresh ? "1" : "0"

  return `#!/usr/bin/env bash
set -euo pipefail

REPO='${shRepo}'
REF='${shRef}'
TOKEN='${shToken}'
FRESH='${shFresh}'

echo ""
echo "  Testing Toolkit - offline agent installer"
echo "  -----------------------------------------"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "  ERROR: Python 3.9+ is required but was not found."
  echo "    macOS:  brew install python   (or install from python.org)"
  echo "    Linux:  sudo apt install python3 python3-venv"
  exit 1
fi

exec "$PY" - "$REPO" "$REF" "$TOKEN" "$FRESH" <<'TT_PYEOF'
import sys, os, json, hashlib, tempfile, shutil, zipfile, subprocess, time, platform, datetime
import threading
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed

repo, ref, token = sys.argv[1], sys.argv[2], sys.argv[3]
fresh = len(sys.argv) > 4 and sys.argv[4] == "1"
api = "https://api.github.com/repos/" + repo + "/contents/"
AUTH_HEADERS = {
    "Authorization": "Bearer " + token,
    "Accept": "application/vnd.github.raw",
    "User-Agent": "TestingToolkit-Installer",
}
MAX_RETRIES = 6
CONCURRENCY = int(os.environ.get("TT_CONCURRENCY") or "4")
# Trace logging is ON by default (the file always gets full detail). Set
# TT_VERBOSE=0 only to quiet the on-console debug echo.
VERBOSE = os.environ.get("TT_VERBOSE") != "0"

# --- logging: console + durable trace file -------------------------------
# Always write a trace-level log to a documented, stable folder shared with the
# offline installer and the agent so failures are always diagnosable:
#   ~/TestingToolkitWeb/logs/installer-<stamp>.log  (this run)
#   ~/TestingToolkitWeb/logs/installer-last.log      (stable, latest run)
_stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
_ws_root = os.environ.get("TT_WORKSPACE_DIR") or os.path.join(
    os.path.expanduser("~"), "TestingToolkitWeb"
)
_log_dir = os.environ.get("TT_LOG_DIR") or os.path.join(_ws_root, "logs")
_log_path = None
_last_log_path = None
_log_fh = None
for _cand in (_log_dir, os.path.join(tempfile.gettempdir(), "TestingToolkitWeb", "logs"), tempfile.gettempdir()):
    try:
        os.makedirs(_cand, exist_ok=True)
        _log_path = os.path.join(_cand, "installer-%s.log" % _stamp)
        _last_log_path = os.path.join(_cand, "installer-last.log")
        _log_fh = open(_log_path, "w", encoding="utf-8")
        break
    except Exception:
        _log_path = None; _last_log_path = None; _log_fh = None

def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        try:
            _log_fh.write(str(msg) + "\\n"); _log_fh.flush()
        except Exception:
            pass

def dbg(msg):
    # File always gets trace detail; console echo gated by VERBOSE.
    if _log_fh:
        try:
            _log_fh.write("    [trace] " + str(msg) + "\\n"); _log_fh.flush()
        except Exception:
            pass
    if VERBOSE:
        print("    [debug] " + str(msg), flush=True)

def _finish_log():
    # Flush + keep a stable copy at installer-last.log, then close the handle.
    global _log_fh
    try:
        if _log_fh:
            _log_fh.flush()
    except Exception:
        pass
    try:
        if _log_path and _last_log_path and os.path.exists(_log_path):
            shutil.copyfile(_log_path, _last_log_path)
    except Exception:
        pass
    try:
        if _log_fh:
            _log_fh.close()
    except Exception:
        pass
    _log_fh = None

def step(m):
    log("")
    log("==> " + m)

# GitHub redirects blobs >1 MB to pre-signed storage; the Authorization header
# must NOT be forwarded across that redirect (storage rejects it). This opener
# strips it on any redirect.
class _StripAuthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for h in ("Authorization", "authorization"):
                try: new.headers.pop(h, None)
                except Exception: pass
                try: new.unredirected_hdrs.pop(h, None)
                except Exception: pass
            dbg("redirect -> %s (dropping Authorization header)" % newurl.split("/")[2])
        return new

_opener = urllib.request.build_opener(_StripAuthRedirect)

def _get(url, headers, timeout=900):
    req = urllib.request.Request(url, headers=headers)
    with _opener.open(req, timeout=timeout) as r:
        return r.read()

def fetch(path):
    return _get(api + path + "?ref=" + ref, AUTH_HEADERS)

# --- Install progress beacon ---------------------------------------------
# A tiny HTTP server on the agent port (127.0.0.1:7842) that reports install
# progress to the web app BEFORE the real agent exists. The bootstrap writes
# download progress here; the offline install.py writes the install/clean/copy
# phases to the SAME file (TT_INSTALL_PROGRESS, default below). The beacon
# serves it at /install/progress and answers /health with 503 "installing" so
# the app never mistakes the beacon for a live agent. It releases the port as
# soon as install.py signals release_port (just before it starts the agent).
# Lives inside the single centralized workspace (~/TestingToolkitWeb/.cache) so
# nothing install-related is left in TMPDIR. Falls back to TMPDIR only if that
# directory cannot be created.
_progress_dir = os.path.join(_ws_root, ".cache")
try:
    os.makedirs(_progress_dir, exist_ok=True)
    PROGRESS_PATH = os.path.join(_progress_dir, "install-progress.json")
except Exception:
    PROGRESS_PATH = os.path.join(tempfile.gettempdir(), "TestingToolkit-install-progress.json")

def write_progress(phase, message, percent=None):
    try:
        d = {"phase": phase, "message": message, "ts": int(time.time() * 1000)}
        if percent is not None:
            d["percent"] = max(0, min(100, int(round(percent))))
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(d))
    except Exception:
        pass

_beacon_stop = threading.Event()

class _BeaconHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/install/progress":
            body = b"{}"
            try:
                with open(PROGRESS_PATH, "rb") as f:
                    body = f.read() or b"{}"
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors(); self.end_headers()
            try: self.wfile.write(body)
            except Exception: pass
        elif path == "/health":
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self._cors(); self.end_headers()
            try: self.wfile.write(b'{"status":"installing"}')
            except Exception: pass
        else:
            self.send_response(404); self._cors(); self.end_headers()

def _beacon_serve():
    # Keep trying to bind: on a reinstall the OLD agent holds the port until
    # install.py kills it, after which we grab it for the rest of the install.
    while not _beacon_stop.is_set():
        try:
            httpd = HTTPServer(("127.0.0.1", 7842), _BeaconHandler)
        except OSError:
            time.sleep(0.5); continue
        httpd.timeout = 0.5
        while not _beacon_stop.is_set():
            try: httpd.handle_request()
            except Exception: pass
        try: httpd.server_close()
        except Exception: pass
        return

def _beacon_watch():
    while not _beacon_stop.is_set():
        try:
            with open(PROGRESS_PATH) as f:
                d = json.loads(f.read() or "{}")
            if d.get("release_port") or d.get("phase") in ("done", "error"):
                _beacon_stop.set(); break
        except Exception:
            pass
        time.sleep(0.4)

threading.Thread(target=_beacon_serve, daemon=True).start()
threading.Thread(target=_beacon_watch, daemon=True).start()

try:
    log("")
    dbg("repo=%s ref=%s concurrency=%d verbose=%s" % (repo, ref, CONCURRENCY, VERBOSE))
    dbg("python %s on %s-%s" % (platform.python_version(), platform.system().lower(), platform.machine().lower()))
    dbg("transcript: %s" % (_log_path if _log_fh else "unavailable"))

    step("Reading bundle manifest")
    dbg("GET " + api + "manifest.json?ref=" + ref)
    manifest = json.loads(fetch("manifest.json").decode("utf-8"))
    parts = manifest["parts"]
    log("    %d parts" % manifest["partCount"])
    if VERBOSE:
        for p in parts:
            dbg("part %s  sha256=%s..." % (p["name"], p["sha256"][:12]))

    # Transient extraction scratch, kept inside the centralized workspace
    # (~/TestingToolkitWeb/.cache/work) and deleted when the install finishes.
    # Falls back to TMPDIR if the workspace dir cannot be created.
    try:
        _work_parent = os.path.join(_ws_root, ".cache", "work")
        os.makedirs(_work_parent, exist_ok=True)
        work = tempfile.mkdtemp(prefix="TestingToolkit_", dir=_work_parent)
    except Exception:
        work = tempfile.mkdtemp(prefix="TestingToolkit_")
    # Stable cache keyed by ref => completed parts survive a re-run (resume).
    # Kept inside the single centralized workspace (~/TestingToolkitWeb/.cache/
    # downloads) so EVERYTHING lives in one place; falls back to TMPDIR if that
    # cannot be created.
    safe_ref = "".join(c if (c.isalnum() or c in "_.-") else "_" for c in ref)
    cache_root = os.path.join(_ws_root, ".cache", "downloads")
    try:
        os.makedirs(cache_root, exist_ok=True)
    except Exception:
        cache_root = os.path.join(tempfile.gettempdir(), "TestingToolkitWeb-cache")
    # On a fresh (reinstall) download, wipe any previously downloaded parts so
    # nothing stale is reused and the whole bundle is fetched again.
    if fresh:
        log("    Fresh reinstall: clearing any previously downloaded parts.")
        dbg("purging cache %s" % cache_root)
        shutil.rmtree(cache_root, ignore_errors=True)
    parts_dir = os.path.join(cache_root, safe_ref)
    os.makedirs(parts_dir, exist_ok=True)
    dbg("work dir: %s" % work)
    dbg("parts cache: %s" % parts_dir)

    step("Downloading agent bundle")

    # A single in-place progress bar (CR-overwritten). Per-part detail still
    # goes to the transcript log; the console stays clean.
    def show_bar(done, total):
        w = 28
        frac = (done / total) if total else 0
        fill = int(round(frac * w))
        if fill > w: fill = w
        if fill < 0: fill = 0
        bar = "#" * fill + "-" * (w - fill)
        sys.stdout.write("\\r    [%s] %3d%%  (%d/%d)   " % (bar, int(frac * 100), done, total))
        sys.stdout.flush()

    def sha256_file(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest().lower()

    def download(p):
        dest = os.path.join(parts_dir, p["name"])
        want = p["sha256"].lower()
        # Resume: reuse a previously downloaded, checksum-valid part.
        if os.path.exists(dest):
            try:
                if sha256_file(dest) == want:
                    return {"name": p["name"], "status": "cached", "bytes": os.path.getsize(dest), "attempts": 0, "secs": 0.0}
                os.remove(dest)
            except Exception:
                pass
        url = api + p["name"] + "?ref=" + ref
        t0 = time.time()
        last = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                dbg("%s attempt %d: GET %s" % (p["name"], attempt, url))
                data = _get(url, AUTH_HEADERS)
                got = hashlib.sha256(data).hexdigest().lower()
                if got != want:
                    raise ValueError("checksum mismatch (got %s..., expected %s...)" % (got[:12], want[:12]))
                with open(dest, "wb") as f:
                    f.write(data)
                return {"name": p["name"], "status": "ok", "bytes": len(data), "attempts": attempt, "secs": time.time() - t0}
            except Exception as e:
                last = str(e)
                dbg("%s attempt %d failed: %s" % (p["name"], attempt, last))
                if os.path.exists(dest):
                    try: os.remove(dest)
                    except Exception: pass
                if attempt == MAX_RETRIES:
                    return {"name": p["name"], "status": "failed", "error": last, "attempts": attempt, "bytes": 0, "secs": time.time() - t0}
                time.sleep(min(30, 2 ** attempt))

    done = 0
    failures = []
    total = len(parts)
    show_bar(0, total)
    write_progress("downloading", "Downloading agent bundle", 5)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(download, p): p for p in parts}
        for fut in as_completed(futs):
            r = fut.result()
            if r["status"] == "failed":
                failures.append(r)
            else:
                done += 1
            seen = done + len(failures)
            show_bar(seen, total)
            # Map download completion onto the first ~55% of the overall bar;
            # the offline install.py owns the remainder.
            write_progress(
                "downloading",
                "Downloading agent bundle (%d/%d parts)" % (seen, total),
                5 + (55.0 * seen / total if total else 0),
            )
    sys.stdout.write("\\n"); sys.stdout.flush()
    if failures:
        for f in failures:
            log("    [x] %s failed: %s" % (f["name"], f["error"]))
        raise RuntimeError(
            "%d part(s) failed to download. Completed parts are cached and will be "
            "skipped when you re-run this installer. See the log: %s" % (len(failures), _log_path))

    step("Reassembling bundle")
    write_progress("extracting", "Reassembling bundle", 61)
    zip_path = os.path.join(work, manifest["archive"])
    with open(zip_path, "wb") as out:
        for p in sorted(parts, key=lambda x: x["name"]):
            with open(os.path.join(parts_dir, p["name"]), "rb") as f:
                shutil.copyfileobj(f, out)
    if sha256_file(zip_path) != manifest["sha256"].lower():
        raise RuntimeError("Final archive checksum mismatch - download may be corrupt. Delete the cache folder and re-run.")
    log("    archive verified")

    step("Extracting")
    write_progress("extracting", "Extracting files", 63)
    dest = os.path.join(work, manifest["extractTo"])
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)

    # --- Overlay the latest Python code on top of the bundle ------------
    # The heavy bundle (wheels/runtime/models) changes rarely; the agent
    # code + installer change often. Pull current source from the repo and
    # lay it over the extracted files so fixes ship without re-packing the
    # whole bundle. Best-effort: fall back to bundled code on any error.
    step("Applying latest agent code")
    write_progress("overlay", "Applying latest agent code", 64)
    try:
        um = json.loads(fetch("agent-update.json").decode("utf-8"))
        src_ref = um.get("ref", ref)
        dbg("overlay ref=%s files=%d" % (src_ref, len(um.get("files", []))))
        n = 0
        for f in um.get("files", []):
            rel = f["path"]
            target = os.path.join(dest, "src", *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as out:
                out.write(_get(f["url"], AUTH_HEADERS, timeout=120))
            n += 1
        ip_url = api + "agent-bundle/install.py?ref=" + src_ref
        with open(os.path.join(dest, "install.py"), "wb") as out:
            out.write(_get(ip_url, AUTH_HEADERS, timeout=120))
        # Overlay requirements.txt so newly-added deps install offline.
        reqs = um.get("requirements")
        if reqs and reqs.get("url"):
            with open(os.path.join(dest, "requirements.txt"), "wb") as out:
                out.write(_get(reqs["url"], AUTH_HEADERS, timeout=120))
        # Drop extra wheels into the extracted wheelhouse for offline pip.
        for w in (um.get("extraWheels") or []):
            wh = os.path.join(dest, "wheelhouse")
            os.makedirs(wh, exist_ok=True)
            with open(os.path.join(wh, w["name"]), "wb") as out:
                out.write(_get(w["url"], AUTH_HEADERS, timeout=120))
        log("    updated %d source files to the latest version" % n)
    except Exception as e:
        log("    (using bundled code; overlay skipped: %s)" % e)

    install_sh = os.path.join(dest, "install.sh")
    install_py = os.path.join(dest, "install.py")
    step("Running offline installer")
    log("    (this part never touches the internet)")
    write_progress("installing_deps", "Starting offline install", 65)
    # Pass the auto-update settings so the agent can fetch future patches.
    env = dict(os.environ)
    env["TT_UPDATE_TOKEN"] = token
    env["TT_UPDATE_REPO"] = repo
    env["TT_UPDATE_REF"] = ref
    # Share the progress file so install.py reports clean/install/copy/start
    # into the same beacon (its default path matches PROGRESS_PATH anyway).
    env["TT_INSTALL_PROGRESS"] = PROGRESS_PATH
    # Forward the resolved log folder so install.py logs into the SAME place.
    if _log_path:
        env["TT_LOG_DIR"] = os.path.dirname(_log_path)
    if os.path.exists(install_sh):
        os.chmod(install_sh, 0o755)
        code = subprocess.call(["bash", install_sh], cwd=dest, env=env)
    else:
        code = subprocess.call([sys.executable, install_py], cwd=dest, env=env)

    shutil.rmtree(work, ignore_errors=True)
    log("")
    if code == 0:
        log("  Done. Testing Toolkit is installed and will start on login.")
    else:
        log("  Installer exited with code %d" % code)
    _finish_log()
    sys.exit(code)
except Exception as e:
    import traceback as _tb
    write_progress("error", "Install failed: %s" % e)
    log("")
    log("  ERROR: %s" % e)
    if _log_fh:
        try: _log_fh.write("    [fatal] " + _tb.format_exc() + "\\n"); _log_fh.flush()
        except Exception: pass
    log("  Debug log: %s" % (_last_log_path or _log_path))
    log("  Nothing was installed. You can safely re-run this installer (finished parts are cached).")
    _finish_log()
    sys.exit(1)
TT_PYEOF
`
}
