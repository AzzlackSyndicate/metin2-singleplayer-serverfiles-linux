# =============================================================================
#  Metin2 server -- one-command installer for Windows.
#
#      irm https://example.com/install.ps1 | iex
#
#  ...or, to pass options:
#
#      iex "& { $(irm https://example.com/install.ps1) } -DryRun"
#
#  THIS INSTALLS A SERVER YOU CAN PLAY ON BY YOURSELF, ON THIS PC.
#
#  Every part of it is bound to 127.0.0.1 -- the address that means "this
#  computer and nothing else". No port is opened to your home network or to the
#  internet, no firewall rule is created, and nobody else can join, not even
#  someone sitting next to you on the same Wi-Fi. That is deliberate: putting a
#  game server on a home connection means handing out your home IP address, and
#  it is not what most people want when they say "I would like to try this".
#
#  If you want a real server that friends can play on, rent a small Linux VPS
#  and use install.sh instead. The last section of the output tells you how.
#
#  What it does, in order:
#
#     1. checks this PC can run it (processor, memory, disk, Windows version)
#     2. makes sure Docker Desktop is installed and running
#     3. downloads the server
#     4. invents a password for the admin panel and two for the database
#     5. builds and starts the server, all on 127.0.0.1
#     6. starts building a game client pointed at 127.0.0.1
#     7. prints the three things you need: client link, panel link, password
#
#  Safe to run twice. If a server is already here it says so and leaves your
#  characters alone.
#
#  ---------------------------------------------------------------------------
#  Written so that a HALF-DOWNLOADED copy cannot do anything: everything is
#  inside functions and the only line that runs anything is the very last one.
#  A truncated download is a parse error, and PowerShell parses the whole thing
#  before running any of it -- so nothing happens at all.
#  ---------------------------------------------------------------------------
#
#  PowerShell 5.1 compatible. It does not need PowerShell 7.
# =============================================================================

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 still negotiates TLS 1.0 by default on some machines,
# and every host worth downloading from turned that off years ago.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

# -----------------------------------------------------------------------------
#  Where the server comes from.
#
#  There is no release archive, and there never will be one. The project holds
#  the Linux port and nothing else -- one 109 KB patch touching 28 files, plus
#  the scripts that turn a checkout into something buildable. Everything
#  copyrighted (the game source, the runtime data tree, the SQL dumps) belongs
#  to Ymir/Webzen and to whoever assembled the r40250 server-file package, and
#  it is not ours to hand out.
#
#  So the server is assembled here, on this PC:
#
#     1. get the project -- a git clone of a few megabytes -- unless a copy is
#        already on this PC
#     2. run linux-port/fetch-sources.sh, which obtains the original r40250
#        package, extracts the source, the game data and the SQL dumps, applies
#        the port, and fills in the Docker build context
#
#  That second script is POSIX shell, and Windows has no shell that can run it.
#  Rather than translate 43 KB of hard-won logic into PowerShell -- and get a
#  second set of bugs for free -- it is run inside a small Debian container, on
#  the Docker that this installer has already made sure is working. The Windows
#  install and the Linux install therefore run exactly the same code, which is
#  worth a great deal when somebody has to debug one of them from a distance.
#
#  Override before running:
#      $env:M2_REPO_URL           = 'https://.../server.git'
#      $env:M2_REPO_DIR           = 'C:\path\to\checkout'
#      $env:M2_SRC_REFERENCE_DIR  = 'C:\path\to\[40250] Reference Serverfile'
#      $env:M2_SRC_ARCHIVE        = 'C:\path\to\the-package.zip'
#      $env:M2_LOCAL_CONTEXT      = 'C:\path\to\linux-port\docker'
# -----------------------------------------------------------------------------
$script:RepoUrl      = if ($env:M2_REPO_URL)          { $env:M2_REPO_URL }          else { 'https://github.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux.git' }
$script:RepoDir      = if ($env:M2_REPO_DIR)          { $env:M2_REPO_DIR }          else { '' }
$script:LocalContext = if ($env:M2_LOCAL_CONTEXT)     { $env:M2_LOCAL_CONTEXT }     else { '' }
$script:SrcArchive   = if ($env:M2_SRC_ARCHIVE)       { $env:M2_SRC_ARCHIVE }       else { '' }
$script:SrcRefDir    = if ($env:M2_SRC_REFERENCE_DIR) { $env:M2_SRC_REFERENCE_DIR } else { '' }
$script:SrcUrl       = if ($env:M2_SRC_URL)           { $env:M2_SRC_URL }           else { '' }

# The download, the unpacked source and the staged tree all live in a Docker
# volume rather than in a folder on C:. Three reasons, all learned by trying the
# other way: it is several times faster than a bind mount into Windows, none of
# those paths then have to fit inside Windows' 260-character limit, and the
# whole lot can be thrown away afterwards with one command.
$script:SrcVolume    = if ($env:M2_SRC_VOLUME) { $env:M2_SRC_VOLUME } else { 'metin2-src-cache' }

# Tagged with a version so that changing the recipe below rebuilds it.
$script:FetcherImage = 'metin2-src-fetcher:1'

# Where this script is, when it is a file at all. Run as `irm ... | iex' it is
# not, and $PSScriptRoot is then empty -- which is exactly the answer we want,
# rather than a wrong guess at a checkout.
$script:SelfDir = if ($PSScriptRoot) { $PSScriptRoot } else { '' }

# State filled in as we go.
$script:InstallDir        = ''
$script:AuthPort          = 11000
$script:GamePorts         = '13000-13002'
$script:PanelPort         = 7788
$script:PanelPassword     = ''
$script:PanelPasswordKnown = $true
$script:PanelPasswordNew   = $true
$script:FreshInstall      = $true
$script:ClientState       = 'unavailable'
$script:ClientLog         = ''
$script:DryRun            = $false
$script:AssumeYes         = $false

# =============================================================================
#  Talking to the human
# =============================================================================

function Write-Step  { param([string]$Text) Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Say   { param([string]$Text) Write-Host "  $Text" }
function Write-Info  { param([string]$Text) Write-Host "  $Text" -ForegroundColor DarkGray }
function Write-Good  { param([string]$Text) Write-Host "  + " -ForegroundColor Green -NoNewline; Write-Host $Text }
function Write-Warn  { param([string]$Text) Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Write-Rule  { Write-Host "  ----------------------------------------------------------------" -ForegroundColor DarkGray }

# Every failure this script knows about comes through here, so the user always
# gets a sentence they can act on rather than a red wall of .NET.
function Stop-Friendly {
    param([string]$Message)
    Write-Host ''
    Write-Host '  Something went wrong.' -ForegroundColor Red
    Write-Host ''
    foreach ($line in ($Message -split "`n")) { Write-Host "  $line" }
    Write-Host ''
    throw (New-Object System.OperationCanceledException 'installer stopped')
}

function Confirm-YesNo {
    param([string]$Question, [bool]$DefaultYes = $true)
    if ($script:AssumeYes) { return $true }
    if (-not [Environment]::UserInteractive) {
        Write-Info "$Question -- running unattended, assuming $(if ($DefaultYes) {'yes'} else {'no'})"
        return $DefaultYes
    }
    $hint = if ($DefaultYes) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $a = Read-Host "  $Question $hint"
        if ([string]::IsNullOrWhiteSpace($a)) { return $DefaultYes }
        switch -Regex ($a.Trim()) {
            '^(y|yes)$' { return $true }
            '^(n|no)$'  { return $false }
            default     { Write-Host '  Please answer y or n.' }
        }
    }
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# Run a program and hand back its exit code and its output, without letting
# anything it prints become a fatal error.
#
# Windows PowerShell turns a native program's standard error into an error
# record as soon as it is redirected with 2>&1, and the $ErrorActionPreference
# = 'Stop' at the top of this file then makes that record *terminating*. Several
# of the docker commands below are questions whose answer is legitimately "no" --
# "does this volume exist?", "does this container exist?" -- and docker says no
# on standard error, with a non-zero exit code. Asked directly, the first such
# question kills the installer with docker's own sentence and no context.
#
# So the preference is lowered for exactly the length of the call, which is the
# only way to ask a native program a yes/no question in PowerShell 5.1 and live
# to read the answer.
function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $File @Arguments 2>&1
        return [pscustomobject]@{ Code = $LASTEXITCODE; Output = (($out | Out-String).Trim()) }
    } finally { $ErrorActionPreference = $previous }
}

# Run docker and let the human watch it, then hand back only the exit code.
#
# The Out-Host is not decoration. PowerShell collects everything a function
# writes to the success stream into that function's return value, so without it
# the "exit code" this returns is an array of several hundred lines of docker
# output -- and `-ne 0' on a non-empty array is true, which turns every
# successful build into a reported failure. Invoke-Compose below carries the
# same note for the same reason; they were the same bug.
function Invoke-DockerLoud {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @Arguments 2>&1 | Out-Host
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

# Copy-Item with a "dir\*" wildcard quietly leaves hidden files behind, and the
# stack's .env.example and .gitignore both start with a dot. Enumerating with
# -Force and copying each entry by its full path picks them all up.
function Copy-DirectoryContents {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in (Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

# =============================================================================
#  Secrets -- all three generated here, on this PC, right now
# =============================================================================

function New-Secret {
    # Never typed by a human, only read by the server. Hex so that it can never
    # contain a space or a quote, which the game's config parser splits on.
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function New-Passphrase {
    # This one gets read off the screen and typed into a browser, so the
    # alphabet leaves out the characters people confuse: 0/O and 1/l/I.
    $alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $bytes = New-Object byte[] 20
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $sb = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) { [void]$sb.Append($alphabet[$b % $alphabet.Length]) }
    return $sb.ToString()
}

# =============================================================================
#  .env handling
#
#  Written with Unix line endings on purpose. A stray carriage return at the
#  end of a value has historically travelled all the way into a database
#  password and produced an "access denied" that nothing explains.
# =============================================================================

function Get-EnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match "^$([regex]::Escape($Key))=(.*)$") { return $Matches[1].Trim() }
    }
    return ''
}

function Set-EnvValue {
    param([string]$Path, [string]$Key, [string]$Value)
    $lines = @()
    if (Test-Path -LiteralPath $Path) { $lines = @([IO.File]::ReadAllLines($Path)) }
    $done = $false
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if (-not $done -and $line -match "^$([regex]::Escape($Key))=") {
            $out.Add("$Key=$Value"); $done = $true
        } else { $out.Add($line) }
    }
    if (-not $done) { $out.Add("$Key=$Value") }
    [IO.File]::WriteAllText($Path, (($out -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

# =============================================================================
#  docker compose, always in the right directory
# =============================================================================

# Both take an explicit array rather than remaining arguments: almost every
# compose argument we pass starts with a dash ("-d", "-T", "--build"), and
# PowerShell would try to bind those as parameters of this function.
function Invoke-Compose {
    param([string[]]$ComposeArgs)
    Push-Location $script:InstallDir
    # docker compose writes its whole build log -- every "=> [ 4/17] RUN ..."
    # line of it -- to standard ERROR, and only the result to standard output.
    # That is normal for it and means nothing is wrong. But a native program's
    # standard error becomes an error record the moment anything downstream is
    # collecting streams, and the $ErrorActionPreference = 'Stop' at the top of
    # this file then makes that record terminating: the first progress line of
    # a perfectly healthy build killed the installer with
    # "Image metin2/game:40250 Building" as its entire explanation.
    #
    # It only showed up when the output was being captured -- run in a console
    # by hand it was invisible -- so it is exactly the kind of thing that would
    # otherwise be found by the first person to write `install.ps1 > log.txt'.
    # Lowered for the length of the call, as with Invoke-Native above.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host, rather than letting the output fall through to the pipeline:
        # PowerShell collects everything a function writes to the success stream
        # into that function's return value. The exit code would then be the
        # *last* of several hundred returned objects, and the caller's `-ne 0'
        # test would compare an array -- which is true whenever the array is
        # non-empty, i.e. after every build that printed anything at all. A
        # perfectly good install reported itself as a failure that way.
        & docker compose @ComposeArgs 2>&1 | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
}

function Invoke-ComposeQuiet {
    param([string[]]$ComposeArgs)
    Push-Location $script:InstallDir
    try {
        return (Invoke-Native 'docker' (@('compose') + $ComposeArgs))
    } finally { Pop-Location }
}

# The compose file names its own project and its own containers, so read them
# out of it rather than assuming. A stack that is renamed -- or a second one
# built for testing -- then still works.
function Get-StackProject {
    $f = Join-Path $script:InstallDir 'docker-compose.yml'
    if (Test-Path -LiteralPath $f) {
        foreach ($line in [IO.File]::ReadAllLines($f)) {
            if ($line -match '^name:\s*(.+?)\s*$') { return $Matches[1].Trim('"', "'") }
        }
    }
    return (Split-Path -Leaf $script:InstallDir)
}

function Get-StackContainerNames {
    $f = Join-Path $script:InstallDir 'docker-compose.yml'
    $names = @()
    if (Test-Path -LiteralPath $f) {
        foreach ($line in [IO.File]::ReadAllLines($f)) {
            if ($line -match '^\s*container_name:\s*(.+?)\s*$') { $names += $Matches[1].Trim('"', "'") }
        }
    }
    return $names
}

# =============================================================================
#  Step 1 -- can this PC run a Metin2 server?
# =============================================================================

function Test-Machine {
    Write-Step 'Checking this PC'

    # --- Windows version. WSL2, which Docker Desktop needs, wants Windows 10
    #     build 19044 (21H2) or newer, or any Windows 11.
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]($os.BuildNumber)
    Write-Info "Windows: $($os.Caption) (build $build)"
    if ($build -lt 19044) {
        Stop-Friendly @"
This is Windows build $build, and Docker Desktop needs build 19044 or newer
(Windows 10 21H2, or Windows 11).

What to do: run Windows Update until it stops offering updates, then run this
installer again. If Windows cannot update this far, the PC is too old for
Docker Desktop and there is no way around that here.
"@
    }

    # --- Processor. The server is built from the original source, which
    #     produces 32-bit x86 programs. There is no ARM build.
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    Write-Info "Processor: $arch"
    if ($arch -match 'ARM') {
        Stop-Friendly @"
This PC has an ARM processor, and the Metin2 server cannot run on it.

This is not something the installer can work around. The server is built from
the original 2000s source code, which produces 32-bit x86 programs. There is
no ARM version, and making one is a large piece of work rather than a setting.

Windows on ARM can emulate x86 programs, but Docker's Linux containers cannot:
the game runs as a Linux program inside Docker, and that layer has no
emulation.

What to do: use a PC with an Intel or AMD processor, or rent a small x86 Linux
VPS (about 5 EUR a month) and use install.sh there instead.
"@
    }
    if ($arch -notmatch 'AMD64|x86') {
        Write-Warn "Unrecognised processor type '$arch'. The server needs Intel or AMD."
        if (-not (Confirm-YesNo 'Carry on anyway?' $false)) { throw (New-Object System.OperationCanceledException 'stopped') }
    }

    # --- Memory. The stack needs ~1 GB to run and more to build, and Windows
    #     plus Docker Desktop plus a browser want their share on top.
    $cs = Get-CimInstance Win32_ComputerSystem
    $ramMb = [int]($cs.TotalPhysicalMemory / 1MB)
    if ($ramMb -lt 3600) {
        Stop-Friendly @"
This PC has about $([math]::Round($ramMb/1024,1)) GB of memory.

The game server alone holds roughly 860 MB, Docker Desktop's Linux virtual
machine takes its own share, and Windows needs the rest. Below 4 GB the build
gets killed part way through, which looks like a random crash and wastes an
afternoon -- so it stops here instead.

What to do: 8 GB is the comfortable size for running this on a PC you are also
playing on. If this PC cannot be upgraded, a small Linux VPS will run it for
about 5 EUR a month -- see install.sh.
"@
    }
    if ($ramMb -lt 7600) {
        Write-Warn "This PC has about $([math]::Round($ramMb/1024,1)) GB of memory."
        Write-Warn 'It will work, but running the server and the game at the same'
        Write-Warn 'time will be tight. 8 GB is the comfortable size.'
        if (-not (Confirm-YesNo 'Carry on?' $true)) { throw (New-Object System.OperationCanceledException 'stopped') }
    } else {
        Write-Info "Memory: $([math]::Round($ramMb/1024,1)) GB -- fine"
    }

    # --- Disk. Docker Desktop keeps its images inside a WSL virtual disk, which
    #     lives on C: unless it has been moved, so C: is what matters even if we
    #     install the stack elsewhere.
    $sysDrive = ($env:SystemDrive)
    $drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$sysDrive'"
    if ($drive) {
        $freeGb = [math]::Round($drive.FreeSpace / 1GB, 1)
        if ($freeGb -lt 15) {
            Stop-Friendly @"
There are only $freeGb GB free on $sysDrive.

This needs about 15 GB: 8 GB to build the server and another 7 GB while the
downloadable game client is put together. Docker Desktop keeps all of it in a
virtual disk on $sysDrive whatever folder you install into. It stops here
rather than filling the drive up and failing three quarters of the way through
a twenty-minute build.

What to do: free up space until there are at least 25 GB, then run this again.
"@
        }
        if ($freeGb -lt 30) {
            Write-Warn "$freeGb GB free on $sysDrive. Enough to build, but the game"
            Write-Warn 'writes roughly 40 MB of logs per hour while it runs.'
        } else {
            Write-Info "Disk: $freeGb GB free on $sysDrive -- fine"
        }
    }

    # --- Virtualisation. Docker Desktop cannot start without it, and the error
    #     it gives when it is off in the BIOS is not helpful.
    try {
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        if ($null -ne $cpu.VirtualizationFirmwareEnabled -and -not $cpu.VirtualizationFirmwareEnabled) {
            Write-Warn 'Hardware virtualisation looks switched off in this PC''s BIOS.'
            Write-Warn 'Docker Desktop cannot start without it. If Docker refuses to'
            Write-Warn 'run later, that is why: reboot into the BIOS and turn on'
            Write-Warn '"Intel VT-x" / "AMD-V" / "SVM Mode".'
        }
    } catch { }

    # Nothing else is needed from Windows itself. Everything that unpacks,
    # patches or copies the server runs inside a container -- see the note at
    # the top of this file -- so there is no tar.exe, no git.exe and no
    # 7-Zip to check for here.
}

# =============================================================================
#  Step 2 -- Docker Desktop
# =============================================================================

function Test-DockerRunning {
    if (-not (Test-Command 'docker')) { return $false }
    try {
        return ((Invoke-Native 'docker' @('info')).Code -eq 0)
    } catch { return $false }
}

function Test-ComposeAvailable {
    try {
        return ((Invoke-Native 'docker' @('compose', 'version')).Code -eq 0)
    } catch { return $false }
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-DockerDesktopExe {
    foreach ($p in @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe")) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Initialize-Docker {
    Write-Step 'Docker Desktop'

    if ((Test-DockerRunning) -and (Test-ComposeAvailable)) {
        Write-Good 'Docker Desktop is installed and running.'
        Write-Info (Invoke-Native 'docker' @('--version')).Output
        return
    }

    $exe = Find-DockerDesktopExe

    # ------------------------------------------------ installed but not started
    if ($exe) {
        Write-Say 'Docker Desktop is installed but not running yet. Starting it...'
        Write-Say 'The first start takes a minute or two while its Linux virtual'
        Write-Say 'machine boots.'
        if ($script:DryRun) { Write-Info "[dry-run] start $exe"; return }
        try { Start-Process -FilePath $exe | Out-Null } catch { }

        $deadline = (Get-Date).AddMinutes(4)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            if ((Test-DockerRunning) -and (Test-ComposeAvailable)) {
                Write-Good 'Docker Desktop is running.'
                return
            }
            Write-Host '.' -NoNewline
        }
        Write-Host ''
        Stop-Friendly @"
Docker Desktop did not finish starting within four minutes.

This is normal on a very first start on a slow PC -- but it can also mean it
is waiting for you to click something.

What to do:
  1. Open Docker Desktop from the Start menu.
  2. Accept its licence terms if it asks, and wait until the whale icon in the
     bottom-left corner stops animating and says "Engine running".
  3. Run this installer again. It will notice Docker is ready and carry on.

If it says virtualisation is not enabled, that is a BIOS setting: reboot into
the BIOS and turn on "Intel VT-x" / "AMD-V" / "SVM Mode".
"@
    }

    # ------------------------------------------------------- not installed yet
    Write-Say 'Docker Desktop is not installed. It is what actually runs the'
    Write-Say 'server: a small Linux system inside Windows. It is free for'
    Write-Say 'personal use and about a 600 MB download.'
    Write-Say ''

    if ($script:DryRun) {
        Write-Info '[dry-run] winget install Docker.DockerDesktop'
        return
    }

    if (-not (Test-Command 'winget')) {
        Stop-Friendly @"
Docker Desktop is not installed, and this PC does not have winget either, so
the installer cannot fetch it for you.

What to do -- it is three steps and none of them are hard:

  1. Go to  https://www.docker.com/products/docker-desktop/
  2. Download and run "Docker Desktop for Windows". Leave every option at its
     default. Make sure "Use WSL 2 instead of Hyper-V" stays ticked.
  3. Restart your PC when it asks, open Docker Desktop once and wait for it to
     say "Engine running".

Then run this installer again -- the same one line -- and it will carry
straight on from here.
"@
    }

    if (-not (Test-IsAdmin)) {
        Stop-Friendly @"
Docker Desktop has to be installed by an administrator, and this PowerShell
window is not running as one.

What to do:
  1. Click the Start button and type: powershell
  2. Right-click "Windows PowerShell" and choose "Run as administrator".
  3. Paste the same one line you used before and press Enter.

Everything after the Docker install works without administrator rights; it is
only this step that needs it.
"@
    }

    if (-not (Confirm-YesNo 'Install Docker Desktop now?' $true)) {
        Stop-Friendly @"
Nothing was installed.

Docker Desktop is required -- it is the piece that actually runs the server.
When you are ready, run this installer again.
"@
    }

    Write-Say 'Installing Docker Desktop. This takes several minutes and the'
    Write-Say 'screen may look idle for a while. Please do not close this window.'

    # Through Invoke-Native, not directly: winget narrates on standard error,
    # and a redirected native standard error under $ErrorActionPreference =
    # 'Stop' is a terminating error -- see the note on Invoke-Native.
    $code = (Invoke-Native 'winget' @('install', '--id', 'Docker.DockerDesktop',
                                      '--exact', '--silent',
                                      '--accept-source-agreements',
                                      '--accept-package-agreements')).Code

    if ($code -ne 0 -and $code -ne -1978335189) {   # -1978335189 = already installed
        Stop-Friendly @"
The automatic Docker Desktop install did not succeed (winget returned $code).

What to do instead -- this always works:

  1. Go to  https://www.docker.com/products/docker-desktop/
  2. Download and run "Docker Desktop for Windows", leaving every option at
     its default.
  3. Restart your PC when it asks, then open Docker Desktop once and wait for
     it to say "Engine running".

Then run this installer again and it will carry on from here.
"@
    }

    # Docker Desktop needs a reboot (or at least a sign-out) on a first install:
    # it adds you to the "docker-users" group, and Windows only reads group
    # membership when you sign in. Carrying on now would fail in a way that
    # looks like a permission bug.
    Write-Host ''
    Write-Good 'Docker Desktop is installed.'
    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor Yellow
    Write-Host '    ONE RESTART, AND THEN YOU ARE ALMOST DONE' -ForegroundColor Yellow
    Write-Host '  ================================================================' -ForegroundColor Yellow
    Write-Host ''
    Write-Say  'Docker Desktop has just given your Windows account permission to'
    Write-Say  'use it, and Windows only notices that when you sign in again.'
    Write-Say  'So the server cannot be installed in this same session.'
    Write-Say  ''
    Write-Say  'Please do this:'
    Write-Say  ''
    Write-Host '    1. Restart your PC.' -ForegroundColor White
    Write-Host '    2. Open Docker Desktop from the Start menu and wait until it' -ForegroundColor White
    Write-Host '       says "Engine running" at the bottom left.' -ForegroundColor White
    Write-Host '    3. Open PowerShell again and paste the same one line.' -ForegroundColor White
    Write-Say  ''
    Write-Say  'The installer will see that Docker is ready and go straight on to'
    Write-Say  'installing the server. Nothing you have done so far is lost.'
    Write-Host ''
    throw (New-Object System.OperationCanceledException 'reboot required')
}

# `ports: !override' in a compose override file needs Compose v2.24 or newer.
# The Windows install binds everything to 127.0.0.1, which means replacing the
# published port list outright, so this is not optional here.
function Test-ComposeOverrideSupported {
    try {
        $v = (Invoke-Native 'docker' @('compose', 'version', '--short')).Output -replace '^v', ''
        $parts = $v.Split('.')
        $maj = [int]$parts[0]; $min = [int]$parts[1]
        return ($maj -gt 2) -or ($maj -eq 2 -and $min -ge 24)
    } catch { return $false }
}

# =============================================================================
#  Step 3 -- get the server onto this PC
# =============================================================================

function Test-ContextComplete {
    param([string]$Dir)
    (Test-Path -LiteralPath (Join-Path $Dir 'docker-compose.yml')) -and
    (Test-Path -LiteralPath (Join-Path $Dir 'game\src\server')) -and
    (Test-Path -LiteralPath (Join-Path $Dir 'panel\app\admin_panel.py')) -and
    (Test-Path -LiteralPath (Join-Path $Dir 'mariadb\initdb.d\dumps'))
}

function Stop-IncompleteContext {
    param([string]$Dir)
    Stop-Friendly @"
The Docker build context in

    $Dir

is not complete: the game source or the database dumps are missing from it.

That is exactly what a bare checkout of the project looks like. The project
contains the Linux port and nothing else -- the game itself is not ours to
publish -- so a checkout has to be filled in before it can be built. This
installer normally does that step itself; run it without M2_LOCAL_CONTEXT and
it will.
"@
}

# =============================================================================
#  Step 3a -- the helper container
#
#  linux-port/fetch-sources.sh is POSIX shell. Windows cannot run it, and the
#  three ways out of that are:
#
#    * translate it into PowerShell. 43 KB of logic that already knows every
#      way MEGA fails -- an anonymous share that answers the API, resolves the
#      filename and then returns 509 for every chunk while megatools retries
#      forever having written nothing. Rewriting that means discovering all of
#      it again, in a second language, and then maintaining two of them.
#
#    * drive WSL. Docker Desktop may be on the Hyper-V backend, and even on the
#      WSL2 backend the distributions it keeps are not general-purpose ones you
#      can run a shell script in. A PC with Docker working and no usable WSL
#      distribution is an ordinary PC, not a broken one.
#
#    * run it in a container, which is what happens here. Docker is already
#      installed and running by this point -- the installer insisted on it two
#      steps ago -- so there is nothing new to install, no elevation, and no
#      chicken-and-egg: the image is stock Debian plus a handful of packages,
#      and does not depend on any part of this project.
#
#  It also means Windows and Linux run the same script, byte for byte.
# =============================================================================

function Initialize-FetcherImage {
    if ((Invoke-Native 'docker' @('image', 'inspect', $script:FetcherImage)).Code -eq 0) {
        Write-Info "helper image $($script:FetcherImage) is already here"
        return
    }

    Write-Say 'Building a small helper image -- Debian plus the handful of'
    Write-Say 'programs that unpack and patch the server files. About a minute,'
    Write-Say 'and only the first time.'

    $dir = Join-Path ([IO.Path]::GetTempPath()) ('m2fetch-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    try {
        # megatools is the reason this is Debian and not Alpine: Alpine has no
        # megatools package in main or community, and without it the default
        # MEGA link cannot be downloaded at all. Debian also makes the "install
        # this package" advice that fetch-sources.sh prints -- which is written
        # in apt-get -- true rather than misleading.
        $dockerfile = @(
            '# Written by install.ps1. Remove it with:'
            "#     docker image rm $($script:FetcherImage)"
            'FROM debian:12-slim'
            'RUN apt-get update \'
            ' && apt-get install -y --no-install-recommends \'
            '      bash tar patch findutils coreutils diffutils grep sed gawk \'
            '      unzip p7zip-full megatools git curl ca-certificates \'
            ' && rm -rf /var/lib/apt/lists/*'
            ''
        ) -join "`n"
        [IO.File]::WriteAllText((Join-Path $dir 'Dockerfile'), $dockerfile,
                                (New-Object System.Text.UTF8Encoding($false)))

        $code = Invoke-DockerLoud @('build', '-t', $script:FetcherImage, $dir)
        if ($code -ne 0) {
            Stop-Friendly @"
The helper image could not be built. The output above says why.

It is a stock Debian image plus a few packages, so the usual cause is that
this PC could not reach Docker Hub or the Debian package mirrors -- a company
proxy, a VPN, or simply no internet at that moment.

Nothing has been installed and nothing has been left running. When the
connection is working, run this installer again.
"@
        }
    } finally {
        Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Good 'Helper image ready.'
}

# A path Docker will accept on the left of a -v. A trailing backslash is the
# one that bites: PowerShell hands native programs a rebuilt command line, and
# "C:\foo\" ends up escaping the quote that follows it.
function ConvertTo-MountPath {
    param([string]$Path)
    return ((Resolve-Path -LiteralPath $Path).ProviderPath.TrimEnd('\'))
}

# Put the project inside the volume, so that everything after this happens on
# the container's own filesystem: no Windows path lengths, no bind-mount
# overhead on half a gigabyte of small files, and the user's own checkout is
# never written into.
function Copy-ProjectIntoVolume {
    Invoke-Native 'docker' @('volume', 'create', $script:SrcVolume) | Out-Null

    if ($script:RepoDir) {
        if (-not (Test-Path -LiteralPath $script:RepoDir -PathType Container)) {
            Stop-Friendly "M2_REPO_DIR points at '$($script:RepoDir)', which is not a folder."
        }
        $src = ConvertTo-MountPath $script:RepoDir
        Write-Say "Taking the project from $src ..."
        # .git is skipped because it can be far larger than the checkout, and
        # docker/game/src because prepare-context.sh rebuilds it anyway -- on a
        # developer's PC that one directory is 300 MB of files we would copy in
        # only to delete.
        $sh = 'set -e; rm -rf /work/repo; mkdir -p /work/repo; ' +
              'tar cf - -C /src --exclude=./.git --exclude=./linux-port/docker/game/src . ' +
              '| tar xf - -C /work/repo; ' +
              'test -f /work/repo/linux-port/fetch-sources.sh'
        $code = Invoke-DockerLoud @(
            'run', '--rm',
            '-v', "$($src):/src:ro",
            '-v', "$($script:SrcVolume):/work",
            $script:FetcherImage, 'bash', '-c', $sh)
        if ($code -ne 0) {
            Stop-Friendly @"
The project could not be copied out of

    $src

Either that folder is not a checkout of this project -- it must be the top of
it, the folder with installer\ and linux-port\ inside -- or Docker Desktop is
not allowed to read it.

If it is on a drive other than C:, open Docker Desktop -> Settings ->
Resources -> File sharing and add the drive, then run this installer again.
"@
        }
        Write-Good 'Project files in place.'
        return
    }

    if ($script:RepoUrl -like 'REPLACE_ME*' -or [string]::IsNullOrWhiteSpace($script:RepoUrl)) {
        Stop-Friendly @"
This copy of the installer does not know where to get the project from -- the
repository URL placeholder was never filled in, which means you are running a
development copy of install.ps1.

Point it at a checkout you already have:

    `$env:M2_REPO_DIR = 'C:\path\to\checkout'

or give it the repository:

    `$env:M2_REPO_URL = 'https://.../server.git'
"@
    }

    Write-Say "Getting the project from $($script:RepoUrl) ..."
    Write-Say 'A few megabytes -- this is the port, not the game.'
    # git runs in the container too, so Windows needs none installed.
    $sh = 'set -e; if [ -d /work/repo/.git ]; then ' +
          'cd /work/repo && git fetch --depth 1 origin && git reset --hard FETCH_HEAD; ' +
          'else rm -rf /work/repo && git clone --depth 1 "$0" /work/repo; fi; ' +
          'test -f /work/repo/linux-port/fetch-sources.sh'
    $code = Invoke-DockerLoud @(
        'run', '--rm',
        '-v', "$($script:SrcVolume):/work",
        $script:FetcherImage, 'bash', '-c', $sh, $script:RepoUrl)
    if ($code -ne 0) {
        Stop-Friendly @"
The project could not be downloaded from

    $($script:RepoUrl)

The output above says why. Check that this PC can reach that address, and that
the address is right.

Nothing has been installed and nothing has been left running.
"@
    }
    Write-Good 'Project files in place.'
}

# Everything the operator may have handed us lives on Windows and has to be
# reachable from inside the container, so each one becomes a read-only mount.
function Invoke-SourceFetch {
    $runArgs = @('run', '--rm', '--name', 'metin2-src-fetch',
                 '-v', "$($script:SrcVolume):/work")
    $tailArgs = @()

    if ($script:SrcRefDir) {
        if (-not (Test-Path -LiteralPath $script:SrcRefDir)) {
            Stop-Friendly "M2_SRC_REFERENCE_DIR points at '$($script:SrcRefDir)', which does not exist."
        }
        $p = ConvertTo-MountPath $script:SrcRefDir
        $runArgs  += @('-v', "$($p):/reference:ro")
        $tailArgs += @('--reference-dir', '/reference')
        Write-Info "using the unpacked server files in $p"
    }
    elseif ($script:SrcArchive) {
        if (-not (Test-Path -LiteralPath $script:SrcArchive -PathType Leaf)) {
            Stop-Friendly "M2_SRC_ARCHIVE points at '$($script:SrcArchive)', which is not a file."
        }
        $f = ConvertTo-MountPath $script:SrcArchive
        # Mounted as a file rather than as its folder, so nothing else in that
        # folder is exposed to the container -- but under its own name, because
        # fetch-sources.sh chooses between unzip, 7z and tar by looking at the
        # extension. Mounted as "/archive/package" a .rar would arrive with no
        # extension at all and fall through to the unzip guess, which cannot
        # read it.
        $leaf = Split-Path -Leaf $f
        $runArgs  += @('-v', "$($f):/archive/$($leaf):ro")
        $tailArgs += @('--archive', "/archive/$leaf")
        Write-Info "using the package $f"

        # The SQL dumps are not inside metin2_server+src.tar.gz -- they are a
        # zip of their own, sitting next to it in Server\. Given only the
        # tarball, fetch-sources.sh looks for that sibling by path, so on
        # Windows it has to be carried into the container as well or the
        # assembly stops one step from the end with "the database cannot be
        # created without it".
        $sibling = Join-Path (Split-Path -Parent $f) 'metin2_mysql_dump.zip'
        if ((Test-Path -LiteralPath $sibling -PathType Leaf) -and
            ($leaf -notlike '*mysql_dump*')) {
            $runArgs += @('-v', "$($sibling):/archive/metin2_mysql_dump.zip:ro")
            Write-Info 'and metin2_mysql_dump.zip from beside it'
        }
    }
    else {
        if ($script:SrcUrl) { $runArgs += @('-e', "M2_SRC_URL=$($script:SrcUrl)") }
        Write-Say 'The original server-file package is about 1.6 GB and is'
        Write-Say 'downloaded once. Half an hour on a fast connection.'
        Write-Say ''
        Write-Say 'It is kept in a Docker volume afterwards, so running this'
        Write-Say 'installer again does not download it a second time.'
    }

    $runArgs += @($script:FetcherImage,
                  'sh', '/work/repo/linux-port/fetch-sources.sh', 'fetch',
                  '--cache', '/work/cache')
    $runArgs += $tailArgs

    Write-Host ''
    try {
        $code = Invoke-DockerLoud $runArgs
    } finally {
        # --rm covers the ordinary exits. This covers Ctrl-C, which leaves the
        # container behind and would then collide by name on the next run.
        Invoke-Native 'docker' @('rm', '-f', 'metin2-src-fetch') | Out-Null
    }

    if ($code -eq 0) {
        Write-Good 'The server is assembled.'
        return
    }

    # fetch-sources.sh gives every kind of failure its own exit code precisely
    # so that this can say something useful rather than "it did not work".
    switch ($code) {
        3 { Stop-Friendly @"
The helper container is missing a program the assembly needs -- the line above
names it. That is a fault in this installer rather than anything you did;
please report it with the output above.
"@ }
        4 { Stop-Friendly @"
The server-file package could not be downloaded.

If the address above is the MEGA share, the overwhelmingly likely reason is
that the share has run out of bandwidth for the day. MEGA gives anonymous
downloads a quota, and once it is spent every request comes back "509 over
quota" -- the link still looks fine, the file name still resolves, and not one
byte arrives. That is not a broken link and there is nothing wrong with this
PC. It clears by itself, usually within a few hours.

Nothing was installed and no server was left half-built, so there is nothing
to undo. Three ways forward:

  - wait a few hours and run this installer again. It carries on from where it
    stopped and re-downloads nothing it already has.

  - download the package yourself -- a browser works, and so does a MEGA
    account of your own -- and then:

        `$env:M2_SRC_ARCHIVE = 'C:\Users\$($env:USERNAME)\Downloads\package.zip'
        irm https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.ps1 | iex

  - if you have already unpacked it:

        `$env:M2_SRC_REFERENCE_DIR = 'C:\path\to\[40250] Reference Serverfile'
"@ }
        5 { Stop-Friendly @"
The server-file package was found, but it is not the r40250 package this port
expects -- something it must contain is not in it, and the line above says
which.

If you pointed the installer at a file or a folder, check that it really is
the "[40250] Reference Serverfile" package: the one with Server\ and Client\
inside it. If it was downloaded, it may have been cut short -- run the
installer again and it will notice.
"@ }
        6 { Stop-Friendly @"
The Linux port does not apply to this source.

That is a precise answer rather than a vague failure: the server-file package
on this PC is not the r40250 one the port was made against. It is not a fault
in the port, and it must not be forced -- a half-applied port compiles happily
and then produces a server that does not work.

What to do: use the r40250 package. Nothing was installed.
"@ }
        7 { Stop-Friendly @"
Docker ran out of disk space while assembling the server.

It needs about 4 GB inside its own virtual disk while it works, on top of the
15 GB for the build itself. Free some space on $($env:SystemDrive) -- or, in
Docker Desktop, Settings -> Resources -> Advanced, give its disk image a
larger limit -- and run this installer again.
"@ }
        8 { Stop-Friendly @"
The build context could not be filled in: the last step failed and its output
is above. Everything before it succeeded, so running this installer again will
not download anything a second time.

This is a bug rather than something you did; please report it with the output
above.
"@ }
        default { Stop-Friendly @"
Assembling the server failed (the helper exited with code $code). Its output
is above.

Nothing was installed and no server was left half-built. Running this
installer again is safe and picks up where this stopped.
"@ }
    }
}

# The finished context is inside the volume. Lift it out with docker cp, which
# is the one copy that has to land on Windows -- and the only reason the
# install folder has to be short.
function Export-BuildContext {
    Write-Say "Copying the server into $($script:InstallDir) ..."
    $created = Invoke-Native 'docker' @('create', '-v', "$($script:SrcVolume):/work",
                                        $script:FetcherImage, 'true')
    if ($created.Code -ne 0 -or -not $created.Output) {
        Stop-Friendly "Docker would not open the volume holding the assembled server:`n$($created.Output)"
    }
    $cid = ($created.Output -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
    try {
        New-Item -ItemType Directory -Force -Path $script:InstallDir | Out-Null
        # The trailing "/." copies the *contents* of the directory, hidden
        # entries included -- .env.example and .gitignore both start with a dot
        # and both belong in the install.
        $code = Invoke-DockerLoud @('cp', "$($cid):/work/repo/linux-port/docker/.",
                                    $script:InstallDir)
        if ($code -ne 0) {
            Stop-Friendly @"
The server files could not be copied into

    $($script:InstallDir)

The most likely cause is Windows' 260-character path limit: the game carries
quest files about 125 characters below whatever folder you choose. Install
somewhere shorter -- C:\Metin2Server, say:

    iex "& { `$(irm https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.ps1) } -InstallDir C:\Metin2Server"
"@
        }
    } finally {
        Invoke-Native 'docker' @('rm', '-f', $cid) | Out-Null
    }
}

# Windows still refuses to create a file whose whole path is longer than 260
# characters unless long-path support has been switched on, and it is off by
# default. The deepest thing in the release -- a quest script buried under
# game/src/serverfiles/share/locale -- sits about 130 characters below the
# install folder, so a long folder name fails a third of the way through the
# copy with a raw .NET error in whatever language Windows happens to be in.
# Caught here, while it can still be explained and acted on.
function Test-InstallPathLength {
    try {
        $v = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
                -Name 'LongPathsEnabled' -ErrorAction SilentlyContinue
        if ($v -and [int]$v.LongPathsEnabled -eq 1) { return }
    } catch { }

    $room = 260 - 130
    if ($script:InstallDir.Length -le $room) { return }

    Stop-Friendly @"
The folder to install into is too deep for Windows:

    $($script:InstallDir)

That path is $($script:InstallDir.Length) characters long. The server carries quest files about 130
characters below whatever folder you choose, and Windows will not create a
file whose full path passes 260 characters -- so the copy would stop part of
the way through with an error that explains nothing.

What to do: install somewhere shorter -- $room characters or fewer. The default
is well inside that:

    -InstallDir C:\Users\$($env:USERNAME)\Metin2Server
"@
}

function Get-Stack {
    Write-Step 'The server files'

    if (Test-Path -LiteralPath (Join-Path $script:InstallDir 'docker-compose.yml')) {
        $script:FreshInstall = $false
        Write-Good "A server is already installed in $($script:InstallDir)."
        Write-Say ''
        Write-Say 'Nothing here will be deleted. Your accounts, characters, items'
        Write-Say 'and guilds live in a Docker volume that this installer never'
        Write-Say 'touches -- only "docker compose down -v" would remove them, and'
        Write-Say 'this script never runs that.'
        Write-Say ''
        if (-not (Confirm-YesNo 'Continue, updating the settings and restarting the server?' $true)) {
            Write-Say ''
            Write-Say 'Left alone. To manage the existing server:'
            Write-Say "    cd `"$($script:InstallDir)`""
            Write-Say '    docker compose ps'
            throw (New-Object System.OperationCanceledException 'user declined')
        }
        return
    }

    Test-InstallPathLength

    # -- 1. a build context somebody prepared earlier -------------------------
    if ($script:LocalContext) {
        if (-not (Test-Path -LiteralPath $script:LocalContext)) {
            Stop-Friendly "M2_LOCAL_CONTEXT points at '$($script:LocalContext)', which does not exist."
        }
        if (-not (Test-ContextComplete $script:LocalContext)) { Stop-IncompleteContext $script:LocalContext }
        Write-Say "Copying the server from $($script:LocalContext) ..."
        if (-not $script:DryRun) {
            New-Item -ItemType Directory -Force -Path $script:InstallDir | Out-Null
            Copy-DirectoryContents $script:LocalContext $script:InstallDir
        }
        Write-Good 'Server files in place.'
        return
    }

    # -- 2. otherwise: get the project, then assemble the server ---------------
    #
    # If this script is itself sitting in a checkout, that is the obvious place
    # to take the project from and it costs nothing to notice. Run through
    # `irm | iex' there is no such thing, and $script:SelfDir is empty.
    if (-not $script:RepoDir -and $script:SelfDir) {
        $candidate = Split-Path -Parent $script:SelfDir
        if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'linux-port\fetch-sources.sh'))) {
            $script:RepoDir = $candidate
            Write-Info "using the checkout this installer is part of: $candidate"
        }
    }

    Write-Say 'The game itself is not part of this project and cannot be -- it'
    Write-Say 'belongs to Ymir/Webzen. What happens now is that the original'
    Write-Say 'r40250 server files are fetched, the Linux port is applied to'
    Write-Say 'them, and the result is turned into something Docker can build.'
    Write-Say ''

    if ($script:DryRun) {
        Write-Info "[dry-run] build $($script:FetcherImage), assemble into volume $($script:SrcVolume)"
        Write-Info "[dry-run] copy the finished context into $($script:InstallDir)"
        return
    }

    Initialize-FetcherImage
    Copy-ProjectIntoVolume
    Invoke-SourceFetch
    Export-BuildContext

    if (-not (Test-ContextComplete $script:InstallDir)) { Stop-IncompleteContext $script:InstallDir }
    Write-Good "Server files in place in $($script:InstallDir)"
}

# =============================================================================
#  Step 4 -- settings and passwords
# =============================================================================

function Write-Configuration {
    Write-Step 'Settings and passwords'

    $envPath = Join-Path $script:InstallDir '.env'

    if ($script:DryRun) {
        Write-Info "[dry-run] write $envPath with freshly generated passwords"
        $script:PanelPassword = '(generated at install time)'
        return
    }

    if (-not (Test-Path -LiteralPath $envPath)) {
        $example = Join-Path $script:InstallDir '.env.example'
        if (Test-Path -LiteralPath $example) {
            # Re-written through our own writer so it ends up with Unix line
            # endings like everything else we put in this file.
            $text = [IO.File]::ReadAllText($example) -replace "`r`n", "`n"
            [IO.File]::WriteAllText($envPath, $text, (New-Object System.Text.UTF8Encoding($false)))
        } else {
            [IO.File]::WriteAllText($envPath, '', (New-Object System.Text.UTF8Encoding($false)))
        }
    }

    # --- database passwords. MariaDB stores the root password inside its data
    #     volume at first start; changing .env afterwards does not change it,
    #     so an existing value must be kept.
    $rootPw = Get-EnvValue $envPath 'M2_DB_ROOT_PASSWORD'
    $dbPw   = Get-EnvValue $envPath 'M2_DB_PASSWORD'
    if (-not $rootPw) { $rootPw = New-Secret; Write-Good 'database root password: generated' }
    else { Write-Info 'database root password: keeping the existing one' }
    if (-not $dbPw)   { $dbPw = New-Secret;   Write-Good 'database password: generated' }
    else { Write-Info 'database password: keeping the existing one' }

    # --- admin panel passphrase.
    #
    # The panel writes a PBKDF2 hash of this into m2panel.conf on its config
    # volume at first start and never regenerates it, because that would
    # invalidate every session cookie. So on a re-install we must report the
    # OLD password, not a shiny new one that would not work.
    $panelPw = Get-EnvValue $envPath 'M2_PANEL_PASSWORD'
    $confVolumeExists = ((Invoke-Native 'docker' @('volume', 'inspect', "$(Get-StackProject)_panel-conf")).Code -eq 0)

    if ($panelPw) {
        $script:PanelPassword = $panelPw
        $script:PanelPasswordKnown = $true
        $script:PanelPasswordNew = $false
        Write-Info 'admin panel password: keeping the existing one'
    } elseif ($confVolumeExists) {
        $script:PanelPassword = ''
        $script:PanelPasswordKnown = $false
        Write-Warn 'There is an admin panel from an earlier install, but its'
        Write-Warn 'password is not recorded here, so it cannot be shown. The'
        Write-Warn 'summary at the end explains how to set a new one.'
    } else {
        $panelPw = New-Passphrase
        $script:PanelPassword = $panelPw
        $script:PanelPasswordKnown = $true
        Write-Good 'admin panel password: generated (shown at the end)'
    }

    Set-EnvValue $envPath 'M2_DB_ROOT_PASSWORD' $rootPw
    Set-EnvValue $envPath 'M2_DB_PASSWORD'      $dbPw
    if ($panelPw) { Set-EnvValue $envPath 'M2_PANEL_PASSWORD' $panelPw }

    # Everything points at this computer and nowhere else.
    Set-EnvValue $envPath 'M2_PUBLIC_ADDRESS'   '127.0.0.1'
    Set-EnvValue $envPath 'M2_CLIENT_ADDRESS'   '127.0.0.1'
    # A Windows install is always local-only, so the panel's introduction should
    # say "nobody else can join" rather than "hand this address out". The panel
    # cannot work that out on its own -- a public Linux server behind nginx also
    # binds it to 127.0.0.1 -- so we state it here.
    Set-EnvValue $envPath 'M2_LOCAL_ONLY'       '1'
    Set-EnvValue $envPath 'M2_AUTH_PORT'        "$($script:AuthPort)"
    Set-EnvValue $envPath 'M2_GAME_PORT_RANGE'  $script:GamePorts
    # The base compose file publishes the panel as "${M2_PANEL_PUBLIC_PORT}:7788",
    # so an address in front of the port is all it takes to bind it to loopback.
    Set-EnvValue $envPath 'M2_PANEL_PUBLIC_PORT' "127.0.0.1:$($script:PanelPort)"

    Write-Good "Settings written to $envPath"
}

function Write-LoopbackOverride {
    # The game's published port range appears twice in the base compose file
    # ("${RANGE}:${RANGE}"), so an address cannot be put in front of it through
    # the environment -- the ports list has to be replaced outright.
    $path = Join-Path $script:InstallDir 'docker-compose.override.yml'

    if ($script:DryRun) { Write-Info "[dry-run] write $path"; return }

    if (-not (Test-ComposeOverrideSupported)) {
        Stop-Friendly @"
This Docker Compose is older than version 2.24, and the installer needs that
version to bind the game ports to this computer only.

Without it the server would be published to your whole network, which is not
what this installer promises.

What to do: open Docker Desktop, let it update itself (Settings -> Software
updates), then run this installer again.
"@
    }

    # Inside the container the channel cores always listen on 13000-13002 --
    # one channel, three ports, decided by the game and not by us; the auth
    # core is likewise always 11000. Only the host side of each mapping is the
    # operator's to choose. Writing $script:GamePorts on *both* sides of the
    # colon published a range that led nowhere: docker accepted the connection
    # and then had nothing behind it to hand the player to, which looks exactly
    # like a server that is up but broken.
    $containerGamePorts = '13000-13002'
    $span = $script:GamePorts.Split('-')
    if ($span.Count -ne 2 -or ([int]$span[1] - [int]$span[0]) -ne 2) {
        Stop-Friendly @"
-GamePorts has to be a range of exactly three ports, like 13000-13002.

This install runs one channel, and one channel occupies three consecutive
ports inside the server. '$($script:GamePorts)' is not three ports, so there would be
nothing behind part of the range.
"@
    }

    $yaml = @(
        '# Written by install.ps1 -- do not edit; re-run the installer instead.'
        '#'
        '# Everything binds 127.0.0.1: this server is reachable from this'
        '# computer and nowhere else. No other PC on your network can see it,'
        '# and neither can anyone on the internet.'
        'services:'
        '  game:'
        '    ports: !override'
        "      - `"127.0.0.1:$($script:AuthPort):11000/tcp`""
        "      - `"127.0.0.1:$($script:GamePorts):$containerGamePorts/tcp`""
        ''
    ) -join "`n"
    [IO.File]::WriteAllText($path, $yaml, (New-Object System.Text.UTF8Encoding($false)))
    Write-Good 'Loopback-only configuration written.'
}

# =============================================================================
#  Step 5 -- build and start
# =============================================================================

function Start-Stack {
    Write-Step 'Building and starting the server'

    if ($script:FreshInstall) {
        Write-Say 'The first run compiles the game server from its original source'
        Write-Say 'code -- about 195 files. Expect 15 to 30 minutes. It only'
        Write-Say 'happens once; starting it again later takes seconds.'
        Write-Say ''
        Write-Say 'You will see a lot of compiler output. That is normal.'
        Write-Say ''
    }

    if ($script:DryRun) {
        Write-Info "[dry-run] docker compose up -d --build in $($script:InstallDir)"
        return
    }

    # The container names are fixed in the compose file, so another copy of
    # this server would collide with a confusing error. Check first.
    foreach ($n in (Get-StackContainerNames)) {
        if ((Invoke-Native 'docker' @('inspect', $n)).Code -eq 0) {
            # The whole label map as JSON, rather than {{index .Config.Labels
            # "com.docker..."}}. Windows PowerShell rebuilds every argument it
            # hands to a native program and mangles the double quotes inside
            # that template, so docker received a template it could not parse
            # and answered on standard error -- which then read as the name of
            # a directory. This form needs no quotes at all.
            $proj = ''
            $labels = Invoke-Native 'docker' @('inspect', '-f', '{{json .Config.Labels}}', $n)
            if ($labels.Code -eq 0 -and $labels.Output) {
                try {
                    $proj = [string]($labels.Output | ConvertFrom-Json).'com.docker.compose.project.working_dir'
                } catch { $proj = '' }
            }
            if ($proj -and ($proj.Trim().TrimEnd('\','/') -ne $script:InstallDir.TrimEnd('\','/'))) {
                Stop-Friendly @"
A container called '$n' already exists and belongs to another copy of this
server, in:

    $proj

Only one Metin2 server can run per PC, because the container names are fixed.
Either use that one, or remove it first:

    cd "$proj"
    docker compose down
"@
            }
        }
    }

    $code = Invoke-Compose @('up', '-d', '--build')
    if ($code -ne 0) {
        Stop-Friendly @"
The server did not build or did not start. The output above says why. The
usual causes, most common first:

  - Docker Desktop ran out of memory or disk during the build. Open its
    Settings -> Resources and give it at least 4 GB of memory.
  - No internet access from the build (it downloads Ubuntu packages).
  - Docker Desktop stopped part way through. Check its whale icon says
    "Engine running".

Nothing was left in a broken state. When you have fixed it, run the installer
again -- the parts that already built are cached, so it picks up where it
stopped rather than starting over.
"@
    }
    Write-Good 'Containers are up.'
}

function Get-ServiceHealth {
    param([string]$Service)
    $r = Invoke-ComposeQuiet @('ps', '-q', $Service)
    $cid = ($r.Output -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if (-not $cid) { return 'missing' }
    $i = Invoke-Native 'docker' @('inspect', '-f', '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}', $cid.Trim())
    # A failed inspect prints its complaint on standard error, which would read
    # as a health status of its own if it were passed straight through.
    if ($i.Code -ne 0 -or -not $i.Output) { return 'unknown' }
    return $i.Output.Trim()
}

function Wait-Healthy {
    Write-Step 'Waiting for the server to come up'

    if ($script:DryRun) { Write-Info '[dry-run] wait for the containers to report healthy'; return }

    Write-Say 'The database imports the shipped world on its very first start,'
    Write-Say 'and the game cores boot one after another. Give it two or three'
    Write-Say 'minutes.'
    Write-Say ''

    $deadline = (Get-Date).AddMinutes(7)
    $last = ''
    while ((Get-Date) -lt $deadline) {
        $db = Get-ServiceHealth 'mariadb'
        $gm = Get-ServiceHealth 'game'
        $pn = Get-ServiceHealth 'panel'
        $now = "db=$db game=$gm panel=$pn"
        if ($now -ne $last) { Write-Info $now; $last = $now }
        if (($gm -in @('healthy','running')) -and ($pn -in @('healthy','running'))) {
            Write-Good 'The server is up.'
            return
        }
        if ($gm -in @('exited','dead')) { break }
        Start-Sleep -Seconds 5
    }

    Write-Warn 'The server has not reported itself healthy yet. It may still be'
    Write-Warn 'starting. Check with:'
    Write-Warn "    cd `"$($script:InstallDir)`""
    Write-Warn '    docker compose ps'
    Write-Warn '    docker compose logs game --tail 50'
}

# =============================================================================
#  Step 6 -- the game client
#
#  Built by a separate compose service that downloads a large archive, patches
#  the address the client connects to (127.0.0.1 here), repacks it and leaves
#  client.zip where the panel serves it. It takes a long time, so it runs in
#  the background and we report honestly on where it got to.
# =============================================================================

function Test-ClientZipPresent {
    $r = Invoke-ComposeQuiet @('exec', '-T', 'panel', 'test', '-f', '/usr/local/m2panel/client.zip')
    return ($r.Code -eq 0)
}

function Start-ClientBuild {
    Write-Step 'The game client'

    if ($script:DryRun) {
        Write-Info '[dry-run] docker compose run --rm client-builder'
        $script:ClientState = 'building'
        return
    }

    if (Test-ClientZipPresent) {
        Write-Good 'A patched client is already in place.'
        $script:ClientState = 'ready'
        return
    }

    # client-builder sits behind the "client" compose profile, so a plain
    # 'config --services' does not list it at all. 'run' turns the profile on by
    # itself, but this check has to ask for it explicitly.
    $svc = Invoke-ComposeQuiet @('--profile', 'client', 'config', '--services')
    if (($svc.Output -split "`n" | ForEach-Object { $_.Trim() }) -notcontains 'client-builder') {
        Write-Warn 'This release does not include the automatic client builder.'
        Write-Warn 'The server works; there is just nothing to download yet.'
        Write-Warn ''
        Write-Warn 'To supply one yourself, put a client.zip whose serverinfo.py'
        Write-Warn 'points at 127.0.0.1 in place with:'
        Write-Warn "    cd `"$($script:InstallDir)`""
        Write-Warn '    docker compose cp .\client.zip panel:/usr/local/m2panel/client.zip'
        Write-Warn '    docker compose restart panel'
        $script:ClientState = 'unavailable'
        return
    }

    $script:ClientLog = Join-Path $script:InstallDir 'client-build.log'
    # The archive the source fetch already downloaded is the SAME file the
    # client builder wants: one package containing both Server/ and Client/.
    # The two keep separate caches, so without this it fetches its own copy --
    # another 1.7 GB, another hour, and one more chance for the share to refuse.
    # The source cache is a Docker volume here rather than a host path, so we
    # mount it read-only and name the file instead of bind-mounting it.
    $reuseArgs = @()
    try {
        $findCmd = 'find /srccache/cache/archive -maxdepth 1 -type f -size +10M ' +
                   "! -name '.megatmp.*' ! -name '*.part' ! -name '*.tmp' " +
                   "! -name '*.meta' 2>/dev/null | sort | head -1"
        $probe = Invoke-Native 'docker' @(
            'run','--rm','-v',"$($script:SrcVolume):/srccache:ro",
            $script:FetcherImage,'sh','-c',$findCmd)
        $cached = ($probe.Output -split "`n" |
                   ForEach-Object { $_.Trim() } |
                   Where-Object { $_ -like '/srccache/*' } |
                   Select-Object -First 1)
        if ($probe.Code -eq 0 -and $cached) {
            # The volume name is safe on a command line; the FILE NAME is not.
            # The archive is published as "[40250] Reference Serverfile-....zip"
            # and Start-Process joins ArgumentList with spaces without quoting
            # anything, so passing it as -e M2_CLIENT_ARCHIVE=<path> tore the
            # value in half and docker read the remainder as a service name
            # ("no such service: Reference"). The compose file already reads
            # this variable from the environment, which has no such problem.
            $reuseArgs = @('-v', "$($script:SrcVolume):/srccache:ro")
            $env:M2_CLIENT_ARCHIVE = $cached
        }
    } catch {
        # Not being able to look is not a reason to fail -- it just downloads.
    }

    Write-Say 'Starting the client build in the background.'
    if ($reuseArgs.Count -gt 0) {
        Write-Say 'It reuses the archive already downloaded for the server, so this'
        Write-Say 'is a repack rather than another download -- but repacking a'
        Write-Say 'gigabyte still takes a while, and longer on a slow disk.'
    } else {
        Write-Say 'It downloads over a gigabyte and then repacks it, so it takes a'
        Write-Say 'while -- often 20 to 60 minutes depending on your connection.'
    }
    Write-Say ''
    Write-Say 'You do not have to wait. The server is usable now; the download'
    Write-Say 'link simply starts working when this finishes.'

    '' | Set-Content -LiteralPath $script:ClientLog -Encoding ASCII
    # The builder bind-mounts ./client-archive so you can hand it a file rather
    # than have it download one. Create it here so Docker does not.
    New-Item -ItemType Directory -Force -Path (Join-Path $script:InstallDir 'client-archive') | Out-Null
    $errFile = $script:ClientLog + '.err'
    try {
        # -T: no pseudo-terminal. Its output goes to a log file, not a console.
        # -PassThru so we get the process back and can watch it below.
        $proc = Start-Process -FilePath 'docker' `
            -ArgumentList (@('compose','run','--rm','-T') + $reuseArgs + @('client-builder')) `
            -WorkingDirectory $script:InstallDir `
            -RedirectStandardOutput $script:ClientLog `
            -RedirectStandardError  $errFile `
            -WindowStyle Hidden -PassThru
    } catch {
        Write-Warn "The client build could not be started: $($_.Exception.Message)"
        $script:ClientState = 'failed'
        return
    }
    $script:ClientState = 'building'

    # Watch it for a minute and a half before saying anything about it.
    #
    # The naive version -- sleep a few seconds, grep the log for "error" --
    # gets it wrong in both directions: it calls a slow but healthy build
    # broken because the image build printed a warning, and it calls a build
    # that died thirty seconds in "still running", so the operator waits an
    # hour for a link that was never coming. Watching the process itself
    # cannot be wrong about which of those happened.
    Write-Host ''
    Write-Host '  watching it for a moment to be sure it really started' -NoNewline
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Write-Host ''
            # Fold stderr into the log before anyone is told where to look.
            # Start-Process cannot send both streams to one file, and docker
            # writes the interesting part -- the reason it refused to start --
            # to stderr. Pointing someone at a log that is empty precisely when
            # something went wrong is worse than not mentioning a log at all.
            if ((Test-Path -LiteralPath $errFile) -and
                ((Get-Item -LiteralPath $errFile).Length -gt 0)) {
                Add-Content -LiteralPath $script:ClientLog -Value ''
                Add-Content -LiteralPath $script:ClientLog -Value '--- error output ---'
                Get-Content -LiteralPath $errFile -ErrorAction SilentlyContinue |
                    Add-Content -LiteralPath $script:ClientLog
                Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
            }
            if (Test-ClientZipPresent) {
                Write-Good 'The client is built and in place -- that was quick.'
                $script:ClientState = 'ready'
            } else {
                Write-Warn 'The client build stopped almost immediately, so there'
                Write-Warn 'is no download yet. The server itself is fine and'
                Write-Warn 'everything else below still applies.'
                Write-Warn ''
                Write-Warn 'The last few lines of the log:'
                foreach ($f in @($script:ClientLog, $errFile)) {
                    if (Test-Path -LiteralPath $f) {
                        foreach ($l in (Get-Content -LiteralPath $f -Tail 8 -ErrorAction SilentlyContinue)) {
                            if ($l.Trim()) { Write-Warn "    $l" }
                        }
                    }
                }
                Write-Warn "Full log: $($script:ClientLog)"
                $script:ClientState = 'failed'
            }
            return
        }
        if (Test-ClientZipPresent) {
            Write-Host ''
            Write-Good 'The client is built and in place.'
            $script:ClientState = 'ready'
            return
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 5
    }
    Write-Host ''
    Write-Good 'Still going after 90 seconds, which is what a real build looks like.'
    Write-Info "Watch it with:  Get-Content -Wait `"$($script:ClientLog)`""
}

# =============================================================================
#  Step 7 -- the summary
#
#  Three things, and they have to be impossible to miss.
# =============================================================================

function Show-Summary {
    $url = "http://127.0.0.1:$($script:PanelPort)"

    Write-Host ''
    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host '    YOUR METIN2 SERVER IS INSTALLED' -ForegroundColor Green
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Write these three things down now.'
    Write-Host ''

    # ------------------------------------------------------------------- 1
    Write-Host '  1. THE GAME CLIENT -- this is what you play with' -ForegroundColor White
    Write-Host ''
    switch ($script:ClientState) {
        'ready' {
            Write-Host "       $url/download" -ForegroundColor Cyan
            Write-Host ''
            Write-Host '     Ready now. It is already set up to connect to the server'
            Write-Host '     on this PC.'
        }
        'building' {
            Write-Host "       $url/download" -ForegroundColor Cyan
            Write-Host ''
            Write-Host '     This link does NOT work yet.' -ForegroundColor Yellow
            Write-Host '     The client is still being built in the background -- it is a'
            Write-Host '     download of over a gigabyte that then has to be repacked, so'
            Write-Host '     give it 20 to 60 minutes.'
            Write-Host ''
            Write-Host '     Until it finishes the page politely says the download is not'
            Write-Host '     ready. Nothing is broken. Check on it with:'
            Write-Host "         Get-Content -Wait `"$($script:ClientLog)`""
        }
        'failed' {
            Write-Host '       (not available -- the build failed)' -ForegroundColor Yellow
            Write-Host ''
            Write-Host '     The client build stopped with an error. The server itself is'
            Write-Host '     fine. The log is at:'
            Write-Host "         $($script:ClientLog)"
        }
        default {
            Write-Host '       (no client yet)' -ForegroundColor Yellow
            Write-Host ''
            Write-Host '     This release has no automatic client builder. Put your own'
            Write-Host '     client.zip in place with:'
            Write-Host "         cd `"$($script:InstallDir)`""
            Write-Host '         docker compose cp .\client.zip panel:/usr/local/m2panel/client.zip'
            Write-Host '         docker compose restart panel'
        }
    }
    Write-Host ''
    Write-Rule
    Write-Host ''

    # ------------------------------------------------------------------- 2
    Write-Host '  2. YOUR ADMIN PANEL -- this is where you run the server' -ForegroundColor White
    Write-Host ''
    Write-Host "       $url" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '     Open that in your browser. It only works on this PC.'
    Write-Host ''
    Write-Rule
    Write-Host ''

    # ------------------------------------------------------------------- 3
    Write-Host '  3. YOUR ADMIN PANEL PASSWORD' -ForegroundColor White
    Write-Host ''
    if ($script:PanelPasswordKnown -and $script:PanelPassword) {
        Write-Host "       $($script:PanelPassword)" -ForegroundColor Cyan
        Write-Host ''
        if ($script:PanelPasswordNew) {
            Write-Host '     Generated on this PC just now, for this server only.'
        } else {
            Write-Host '     This is the password from when the server was first'
            Write-Host '     installed. It has not been changed.'
        }
        Write-Host "     It is also kept in $(Join-Path $script:InstallDir '.env')"
        Write-Host '     so you can look it up again.'
    } else {
        Write-Host '       (unknown -- this server was installed before)' -ForegroundColor Yellow
        Write-Host ''
        Write-Host '     The panel keeps only a one-way hash of its password, so it'
        Write-Host '     cannot be recovered. To set a new one:'
        Write-Host ''
        Write-Host "         cd `"$($script:InstallDir)`""
        Write-Host '         docker compose exec panel rm /usr/local/etc/m2panel.conf'
        Write-Host '         docker compose restart panel'
        Write-Host '         docker compose logs panel | Select-String -Context 0,4 "ADMIN PANEL PASSWORD"'
    }
    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host ''

    # --------------------------------------------------- the important caveat
    Write-Host '  ================================================================' -ForegroundColor Yellow
    Write-Host '    THIS SERVER IS FOR YOU ALONE' -ForegroundColor Yellow
    Write-Host '  ================================================================' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Everything installed here listens on 127.0.0.1, which means'
    Write-Host '  "this computer and nothing else".'
    Write-Host ''
    Write-Host '    - Nobody else can join. Not your friends over the internet,'
    Write-Host '      and not someone on the same Wi-Fi in the same room.'
    Write-Host '    - No port was opened. No firewall rule was created. Nothing'
    Write-Host '      about this PC is now reachable that was not before.'
    Write-Host '    - Your home IP address has not been given out to anyone.'
    Write-Host ''
    Write-Host '  That is on purpose. It lets you play, build your server and try'
    Write-Host '  things out with no risk at all.'
    Write-Host ''
    Write-Host '  If you later want friends to play on it:' -ForegroundColor White
    Write-Host ''
    Write-Host '    Do not open ports on your home router. A home connection means'
    Write-Host '    handing your home address to every player, your upload speed is'
    Write-Host '    the bottleneck, and the server disappears whenever the PC does.'
    Write-Host ''
    Write-Host '    Rent a small Linux VPS instead -- 4 GB of memory is about 5 EUR'
    Write-Host '    a month at Hetzner, Contabo or Netcup -- and run one line on it:'
    Write-Host ''
    Write-Host '        curl -fsSL https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.sh | sudo sh' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    That installer does the opposite of this one: it publishes the'
    Write-Host '    game to the internet, opens the firewall, and can put a real'
    Write-Host '    HTTPS certificate on the admin panel. Your characters here can'
    Write-Host '    be moved across with a database backup.'
    Write-Host ''

    # ------------------------------------------------------------ day to day
    Write-Host '  Day to day' -ForegroundColor White
    Write-Host ''
    Write-Host "     cd `"$($script:InstallDir)`""
    Write-Host '     docker compose ps                 what is running'
    Write-Host '     docker compose logs -f game       watch the game log'
    Write-Host '     docker compose restart            restart everything'
    Write-Host '     docker compose down               stop (keeps all player data)'
    Write-Host '     docker compose up -d              start again'
    Write-Host ''
    Write-Host '     The one dangerous command is "docker compose down -v".' -ForegroundColor Yellow
    Write-Host '     The -v deletes every account, character and item, with no undo.'
    Write-Host ''
    Write-Host '     The original server files are kept in a Docker volume, so'
    Write-Host '     re-installing never downloads them twice. Once you are happy'
    Write-Host '     with the server you can have those few gigabytes back:'
    Write-Host "         docker volume rm $($script:SrcVolume)"
    Write-Host "         docker image rm $($script:FetcherImage)"
    Write-Host ''
    Write-Host '     Docker Desktop must be running for the server to be up. It'
    Write-Host '     starts with Windows by default, and the server comes back with'
    Write-Host '     it, so after a reboot the panel link just works again.'
    Write-Host ''
    Write-Host '  Two accounts already exist for testing:' -ForegroundColor White
    Write-Host '     admin / 123456789        test / 123456789'
    Write-Host ''
}

# =============================================================================
#  main
# =============================================================================

function Invoke-Metin2Install {
    [CmdletBinding()]
    param(
        [switch]$DryRun,
        [switch]$Yes,
        [string]$InstallDir = '',
        [int]$AuthPort = 0,
        [string]$GamePorts = '',
        [int]$PanelPort = 0,
        [string]$RepoDir = '',
        [string]$RepoUrl = '',
        [string]$ReferenceDir = '',
        [string]$Archive = '',
        [string]$LocalContext = '',
        [switch]$Help
    )

    if ($Help) {
        Write-Host @'

  Metin2 server installer (Windows)

    irm https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.ps1 | iex

  With options:

    iex "& { $(irm https://raw.githubusercontent.com/AzzlackSyndicate/metin2-singleplayer-serverfiles-linux/main/installer/install.ps1) } -DryRun"

    -DryRun            show what would happen, change nothing
    -Yes               don't ask anything; accept every default
    -InstallDir PATH   where to install (default: %USERPROFILE%\Metin2Server)
    -AuthPort N        login port (default: 11000)
    -GamePorts A-B     channel ports (default: 13000-13002)
    -PanelPort N       admin panel port (default: 7788)
    -Help              this text

  Where the server comes from:

    -ReferenceDir DIR  an already-unpacked "[40250] Reference Serverfile"
                       folder (the one with Server\ in it). Nothing is
                       downloaded when you give this.
    -Archive PATH      the server-file package as you downloaded it -- the
                       .zip/.rar/.7z, or metin2_server+src.tar.gz
    -RepoDir PATH      use this checkout of the project instead of cloning one
    -RepoUrl URL       clone the project from here
    -LocalContext DIR  skip all of the above: install from a Docker build
                       context that is already prepared

  Environment variables -- the same things, for when an option is awkward
  to pass through "irm ... | iex":

    $env:M2_REPO_URL           $env:M2_REPO_DIR
    $env:M2_SRC_REFERENCE_DIR  $env:M2_SRC_ARCHIVE   $env:M2_SRC_URL
    $env:M2_LOCAL_CONTEXT      $env:M2_SRC_VOLUME

  Everything installs bound to 127.0.0.1. Nobody else can connect.

'@
        return
    }

    $script:DryRun    = [bool]$DryRun
    $script:AssumeYes = [bool]$Yes
    $script:InstallDir = if ($InstallDir) { $InstallDir }
                         elseif ($env:M2_INSTALL_DIR) { $env:M2_INSTALL_DIR }
                         else { Join-Path $env:USERPROFILE 'Metin2Server' }
    if ($AuthPort  -gt 0) { $script:AuthPort  = $AuthPort }
    if ($GamePorts)       { $script:GamePorts = $GamePorts }
    if ($PanelPort -gt 0) { $script:PanelPort = $PanelPort }
    # A command-line option beats the environment variable of the same name.
    if ($RepoDir)      { $script:RepoDir      = $RepoDir }
    if ($RepoUrl)      { $script:RepoUrl      = $RepoUrl }
    if ($ReferenceDir) { $script:SrcRefDir    = $ReferenceDir }
    if ($Archive)      { $script:SrcArchive   = $Archive }
    if ($LocalContext) { $script:LocalContext = $LocalContext }

    Write-Host ''
    Write-Host '  Metin2 server installer' -ForegroundColor White
    Write-Host '  for Windows -- a private server on this PC' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Everything will be bound to 127.0.0.1: this PC and nothing else.'
    Write-Host '  Nobody else will be able to connect, and no port will be opened.'
    if ($script:DryRun) {
        Write-Host ''
        Write-Host '  ** DRY RUN -- nothing on this PC will be changed **' -ForegroundColor Yellow
    }

    try {
        Test-Machine
        Initialize-Docker
        Get-Stack
        Write-Configuration
        Write-LoopbackOverride
        Start-Stack
        Wait-Healthy
        Start-ClientBuild
        Show-Summary
    }
    catch [System.OperationCanceledException] {
        # Every message the user needs was already printed by whoever threw.
        return
    }
    catch {
        # The safety net. Nobody should ever see a raw .NET stack trace from
        # an installer aimed at people who have never opened PowerShell before.
        Write-Host ''
        Write-Host '  Something unexpected went wrong.' -ForegroundColor Red
        Write-Host ''
        Write-Host "  $($_.Exception.Message)"
        Write-Host ''
        Write-Host '  Nothing on this PC has been left half-finished in a way that'
        Write-Host '  stops you trying again -- running the same one line a second'
        Write-Host '  time is safe and picks up where this left off.'
        Write-Host ''
        Write-Host '  If it keeps happening, this is the detail to report:'
        Write-Host ''
        Write-Host "    $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
        Write-Host "    $($_.CategoryInfo.Category) / $($_.FullyQualifiedErrorId)" -ForegroundColor DarkGray
        Write-Host ''
        return
    }
}

Invoke-Metin2Install @args
