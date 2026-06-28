/**
 * Builds the tiny self-contained Windows installer (a .cmd file).
 *
 * The file is a cmd/PowerShell polyglot. The batch header (which cmd runs)
 * extracts everything after the `#PSBEGIN` marker into a temporary .ps1 file
 * and executes it with `powershell -File`. cmd stops at `exit /b`, so it never
 * tries to parse the PowerShell body below it.
 *
 * IMPORTANT: the marker is searched for as the concatenation 'PSB'+'EGIN' so
 * that the literal token `#PSBEGIN` appears EXACTLY ONCE in the whole file (the
 * real marker). An earlier version searched for the literal '#PSBEGIN', which
 * also matched the search command itself, so extraction started mid-line and
 * the PowerShell failed to parse. Running via a real .ps1 file with -File also
 * avoids Invoke-Expression quoting/scoping issues and any script-size limits.
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
 *   - Every part is cached under %TEMP%\\TestingToolkit-cache\\<ref> and skipped
 *     on re-run if its checksum already matches (true resume).
 *   - A full transcript is written to %TEMP% so failures can be diagnosed.
 *     Set TT_VERBOSE=1 before running for per-attempt / proxy / redirect detail.
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

  return `@echo off
setlocal
set "_TT_PS1=%TEMP%\\TestingToolkit_%RANDOM%%RANDOM%.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$marker='#PS'+'BEGIN'; $c=[IO.File]::ReadAllText('%~f0'); $start=$c.IndexOf([char]10, $c.IndexOf($marker)) + 1; [IO.File]::WriteAllText($env:_TT_PS1, $c.Substring($start), [Text.UTF8Encoding]::new($false))"
rem Relaunch the install hidden so there is no console window: the web app is
rem the UI and shows live progress via the beacon. The hidden PowerShell
rem deletes its own temp .ps1 when it finishes (it must outlive this cmd).
if not "%TT_HIDDEN%"=="1" (
  set "TT_HIDDEN=1"
  start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%_TT_PS1%"
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%_TT_PS1%"
set "_TT_CODE=%ERRORLEVEL%"
del "%_TT_PS1%" >nul 2>&1
exit /b %_TT_CODE%
#PSBEGIN
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

$Repo  = '${psRepo}'
$Ref   = '${psRef}'
$Token = '${psToken}'
$ApiBase = 'https://api.github.com/repos/' + $Repo + '/contents/'
$Concurrency = 4
if ($env:TT_CONCURRENCY -match '^[1-9][0-9]*$') { $Concurrency = [int]$env:TT_CONCURRENCY }
$MaxRetries  = 6
$Verbose = ($env:TT_VERBOSE -eq '1')
# Fresh install: ignore any previously downloaded parts and pull everything
# again from scratch. Injected by the server for reinstall downloads.
$Fresh = ${fresh ? "$true" : "$false"}

# --- Console + transcript logging ----------------------------------------
$LogFile = Join-Path $env:TEMP ('TestingToolkit-installer-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$Transcribing = $false
try { Start-Transcript -Path $LogFile -Force | Out-Null; $Transcribing = $true } catch {}

function Write-Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Dbg($m)  { if ($Verbose) { Write-Host ("    [debug] " + $m) -ForegroundColor DarkGray } }
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
$ProgressPath = Join-Path $env:TEMP 'TestingToolkit-install-progress.json'

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
  Write-Dbg ("transcript: " + $(if ($Transcribing) { $LogFile } else { 'unavailable' }))

  # GitHub API headers. 'application/vnd.github.raw' returns the file bytes
  # directly. The token is read-only and scoped to this single repo.
  $headers = @{
    'Authorization' = 'Bearer ' + $Token
    'Accept'        = 'application/vnd.github.raw'
    'User-Agent'    = 'TestingToolkit-Installer'
  }

  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
  $work = Join-Path $env:TEMP ('TestingToolkit_' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  # Stable cache keyed by ref => completed parts survive a re-run (true resume).
  $cacheRoot = Join-Path $env:TEMP 'TestingToolkit-cache'
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
  } else {
    Write-Host ("  Installer exited with code " + $code) -ForegroundColor Yellow
  }
} catch {
  Set-TtProgress 'error' ("Install failed: " + $_.Exception.Message)
  Write-Host ""
  Write-Host ("  ERROR: " + $_.Exception.Message) -ForegroundColor Red
  Write-Host ("  Debug log: " + $LogFile) -ForegroundColor Yellow
  Write-Host "  Nothing was installed. You can safely re-run this installer (finished parts are cached)."
} finally {
  # Stop the progress beacon (it usually exits on its own once install.py
  # signals release_port, but force it down in case of an early failure).
  try { if ($beaconPs) { $beaconPs.Stop() } } catch {}
  try { if ($beaconRs) { $beaconRs.Close() } } catch {}
  if ($Transcribing) { try { Stop-Transcript | Out-Null } catch {} }
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
\`
}
`
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
 * rejects >1 MB blobs), completed parts are cached under
 * $TMPDIR/TestingToolkit-cache/<ref> for true resume, every part is logged in
 * real time, and a full transcript is written to a log file. Set TT_VERBOSE=1
 * for per-attempt / redirect detail.
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
VERBOSE = os.environ.get("TT_VERBOSE") == "1"

# --- logging: console + transcript file ----------------------------------
_log_path = os.path.join(
    tempfile.gettempdir(),
    "TestingToolkit-installer-%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
)
_log_fh = None
try:
    _log_fh = open(_log_path, "w", encoding="utf-8")
except Exception:
    _log_fh = None

def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        try:
            _log_fh.write(msg + "\\n"); _log_fh.flush()
        except Exception:
            pass

def dbg(msg):
    if VERBOSE:
        log("    [debug] " + msg)

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

    work = tempfile.mkdtemp(prefix="TestingToolkit_")
    # Stable cache keyed by ref => completed parts survive a re-run (resume).
    safe_ref = "".join(c if (c.isalnum() or c in "_.-") else "_" for c in ref)
    cache_root = os.path.join(tempfile.gettempdir(), "TestingToolkit-cache")
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
    if _log_fh:
        try: _log_fh.close()
        except Exception: pass
    sys.exit(code)
except Exception as e:
    write_progress("error", "Install failed: %s" % e)
    log("")
    log("  ERROR: %s" % e)
    log("  Debug log: %s" % _log_path)
    log("  Nothing was installed. You can safely re-run this installer (finished parts are cached).")
    if _log_fh:
        try: _log_fh.close()
        except Exception: pass
    sys.exit(1)
TT_PYEOF
`
}
