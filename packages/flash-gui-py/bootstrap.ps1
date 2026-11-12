param(
    [switch]$Setup,
    [switch]$Demo,
    [switch]$ServeOnly
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$script:LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
try { Start-Transcript -Path (Join-Path $script:LogDir 'bootstrap.log') -Append -Force | Out-Null } catch { }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:SetupDir = Join-Path $Root 'setup'
$script:WebDir = Join-Path $Root 'web'
$script:StatePath = Join-Path $Root 'state.json'
$script:LogDir = Join-Path $Root 'logs'
$script:VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$script:DepsLog = Join-Path $script:LogDir 'setup-deps.log'
$script:DepsErr = Join-Path $script:LogDir 'setup-deps.err.log'

$script:PythonExe = $null
$script:PythonVersion = ''
$script:InstallDir = ''
$script:DepsOk = $false
$script:ToolOk = $false
$script:ToolInfo = @{ ok = $false; path = ''; source = 'none'; zip = ''; message = '' }
$script:PythonProc = $null
$script:DownloadProc = $null
$script:DownloadError = ''
$script:PipProc = $null
$script:Task = ''
$script:SetupBrowser = $null
$script:Shutdown = $false

# ---------------- 检测 ----------------

function Test-PythonExe([string]$Exe) {
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $version = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $version) { return $false }
        return ([version]$version -ge [version]'3.9')
    } catch {
        return $false
    }
}

function Get-SavedPython {
    if (-not (Test-Path -LiteralPath $script:StatePath)) { return $null }
    try {
        $state = (Get-Content -LiteralPath $script:StatePath -Raw -Encoding UTF8) | ConvertFrom-Json
        if ($state.python_exe) { return [string]$state.python_exe }
    } catch { }
    return $null
}

function Set-StateValue([string]$Key, [string]$Value) {
    $state = @{}
    if (Test-Path -LiteralPath $script:StatePath) {
        try {
            $saved = (Get-Content -LiteralPath $script:StatePath -Raw -Encoding UTF8) | ConvertFrom-Json
            foreach ($property in $saved.PSObject.Properties) { $state[$property.Name] = $property.Value }
        } catch { }
    }
    if ($Value) { $state[$Key] = $Value } else { $state.Remove($Key) }
    ($state | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $script:StatePath -Encoding UTF8
}

function Save-SavedPython([string]$Exe) {
    Set-StateValue 'python_exe' $Exe
}

function Get-SavedInstallDir {
    if (-not (Test-Path -LiteralPath $script:StatePath)) { return '' }
    try {
        $state = (Get-Content -LiteralPath $script:StatePath -Raw -Encoding UTF8) | ConvertFrom-Json
        if ($state.python_install_dir) { return [string]$state.python_install_dir }
    } catch { }
    return ''
}

function Save-SavedInstallDir([string]$Dir) {
    Set-StateValue 'python_install_dir' $Dir
}

function Get-PythonExe {
    $saved = Get-SavedPython
    if ($saved -and (Test-PythonExe $saved)) { return $saved }

    if ($script:InstallDir) {
        $custom = Join-Path $script:InstallDir 'python.exe'
        if (Test-PythonExe $custom) { return $custom }
    }

    $candidates = New-Object System.Collections.Generic.List[string]

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher -and $launcher.Source -notlike '*WindowsApps*') {
        try {
            $exe = & py -3 -c "import sys; print(sys.executable)" 2>$null
            if ($exe) { $candidates.Add($exe) }
        } catch { }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*WindowsApps*') { $candidates.Add($python.Source) }

    $patterns = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )
    foreach ($pattern in $patterns) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (Test-PythonExe $exe) { return $exe }
    }
    return $null
}

function Test-VenvDeps {
    if (-not (Test-Path -LiteralPath $script:VenvPython)) { return $false }
    & $script:VenvPython -c "import webview, paramiko" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-Installer {
    $dir = Join-Path $Root 'installer'
    if (-not (Test-Path $dir)) { return $null }
    return Get-ChildItem -Path $dir -Filter 'python-*-amd64.exe' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
}

function Find-ToolExe {
    $tools = Join-Path $Root 'tools'
    if (-not (Test-Path -LiteralPath $tools)) { return '' }
    $preferred = @(
        (Join-Path $tools 'Amlogic_Flash_Tool_v6.0.0\bin\update.exe'),
        (Join-Path $tools 'update.exe')
    )
    foreach ($path in $preferred) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $found = Get-ChildItem -LiteralPath $tools -Recurse -Filter 'update.exe' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return ''
}

function Ensure-Tool {
    $tools = Join-Path $Root 'tools'
    $exe = Find-ToolExe
    $archive = $null
    if (Test-Path -LiteralPath $tools) {
        $archive = Get-ChildItem -LiteralPath $tools -Filter '*.zip' -ErrorAction SilentlyContinue |
            Sort-Object Name | Select-Object -First 1
    }

    if ($exe) {
        if ($archive) {
            return @{ ok = $true; path = $exe; source = 'zip'; zip = $archive.Name; message = '' }
        }
        return @{ ok = $true; path = $exe; source = 'folder'; zip = ''; message = '' }
    }
    if (-not (Test-Path -LiteralPath $tools)) {
        return @{ ok = $false; path = ''; source = 'none'; zip = ''; message = '' }
    }
    if (-not $archive) {
        return @{ ok = $false; path = ''; source = 'none'; zip = ''; message = '' }
    }

    try {
        Expand-Archive -LiteralPath $archive.FullName -DestinationPath $tools -Force -ErrorAction Stop
    } catch {
        return @{ ok = $false; path = ''; source = 'zip'; zip = $archive.Name; message = "解压失败：$($_.Exception.Message)" }
    }

    $exe = Find-ToolExe
    if ($exe) {
        return @{ ok = $true; path = $exe; source = 'zip'; zip = $archive.Name; message = '' }
    }
    return @{ ok = $false; path = ''; source = 'zip'; zip = $archive.Name; message = '压缩包里没有找到 update.exe' }
}

function Refresh-State {
    $script:PythonExe = Get-PythonExe
    $script:PythonVersion = ''
    if ($script:PythonExe) {
        $script:PythonVersion = & $script:PythonExe -c "import sys; print(sys.version.split()[0])" 2>$null
    }
    $script:DepsOk = Test-VenvDeps
    $script:ToolInfo = Ensure-Tool
    $script:ToolOk = [bool]$script:ToolInfo.ok
}

function Update-Processes {
    if ($script:PythonProc -and $script:PythonProc.HasExited) {
        try { $script:PythonProc.WaitForExit() } catch { }
        $script:PythonProc = $null
        $script:Task = ''
        Refresh-State
        if (-not $script:PythonExe -and $script:InstallDir) {
            $candidate = Join-Path $script:InstallDir 'python.exe'
            if (Test-PythonExe $candidate) {
                Save-SavedPython $candidate
                Refresh-State
            }
        }
    }
    if ($script:DownloadProc -and $script:DownloadProc.HasExited) {
        try { $script:DownloadProc.WaitForExit() } catch { }
        $script:DownloadProc = $null
        $script:Task = ''
        $installer = Get-Installer
        if ($installer -and $installer.Length -gt 20MB) {
            $script:DownloadError = ''
            Start-PythonInstall | Out-Null
        } else {
            $script:DownloadError = '下载失败，请检查本机网络，或手动把 python-3.12.7-amd64.exe 放到 installer 目录。'
        }
    }
    if ($script:PipProc -and $script:PipProc.HasExited) {
        try { $script:PipProc.WaitForExit() } catch { }
        $script:PipProc = $null
        $script:Task = ''
        Refresh-State
    }
}

function Get-DownloadProgress {
    if ($script:Task -ne 'python-download') { return 0 }
    $dest = Join-Path $Root 'installer\python-3.12.7-amd64.exe'
    if (-not (Test-Path -LiteralPath $dest)) { return 0 }
    $size = (Get-Item -LiteralPath $dest).Length
    $percent = [int]($size * 100 / 26538304)
    if ($percent -gt 99) { $percent = 99 }
    return $percent
}

# ---------------- 安装 ----------------

function Start-PythonDownload {
    $installerDir = Join-Path $Root 'installer'
    New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
    $dest = Join-Path $installerDir 'python-3.12.7-amd64.exe'
    $script:DownloadError = ''
    $script:Task = 'python-download'

    try {
        $script:DownloadProc = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + (Join-Path $Root 'installer\download_python.ps1') + '"'),
            ('"' + $dest + '"')
        ) -PassThru -WindowStyle Hidden
        return @{ ok = $true }
    } catch {
        $script:Task = ''
        $script:DownloadError = $_.Exception.Message
        return @{ ok = $false; message = $_.Exception.Message }
    }
}

function Start-PythonInstall {
    Update-Processes
    if ($script:Task) { return @{ ok = $false; message = '已经有安装任务在进行中' } }

    $installer = Get-Installer
    if (-not $installer) {
        return Start-PythonDownload
    }

    $arguments = @(
        '/passive',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_pip=1',
        'Include_tcltk=1',
        'Include_test=0',
        'Include_doc=0',
        'Include_launcher=1',
        'Shortcuts=0'
    )
    if ($script:InstallDir) {
        New-Item -ItemType Directory -Force -Path $script:InstallDir | Out-Null
        $arguments += ('TargetDir="{0}"' -f $script:InstallDir)
    }
    try {
        $script:PythonProc = Start-Process -FilePath $installer.FullName -ArgumentList $arguments -PassThru
        $script:Task = 'python'
        return @{ ok = $true }
    } catch {
        return @{ ok = $false; message = $_.Exception.Message }
    }
}

function Start-DepsInstall {
    Update-Processes
    if ($script:Task) { return @{ ok = $false; message = '已经有安装任务在进行中' } }
    if (-not $script:PythonExe) { return @{ ok = $false; message = '请先安装 Python' } }

    if (-not (Test-Path -LiteralPath $script:VenvPython)) {
        New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
        "正在创建虚拟环境 .venv …" | Set-Content -LiteralPath $script:DepsLog -Encoding UTF8
        & $script:PythonExe -m venv (Join-Path $Root '.venv') 2>&1 |
            ForEach-Object { $_ | Add-Content -LiteralPath $script:DepsLog -Encoding UTF8 }
        if (-not (Test-Path -LiteralPath $script:VenvPython)) {
            return @{ ok = $false; message = '创建虚拟环境失败，请检查 Python 安装是否完整' }
        }
    }

    New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
    "开始安装界面依赖…" | Set-Content -LiteralPath $script:DepsLog -Encoding UTF8
    Remove-Item -LiteralPath $script:DepsErr -Force -ErrorAction SilentlyContinue

    $arguments = @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
        '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple',
        '--extra-index-url', 'https://mirrors.aliyun.com/pypi/simple',
        'pywebview', 'paramiko'
    )

    try {
        $script:PipProc = Start-Process -FilePath $script:VenvPython -ArgumentList $arguments `
            -RedirectStandardOutput $script:DepsLog -RedirectStandardError $script:DepsErr `
            -PassThru -WindowStyle Hidden
        $script:Task = 'deps'
        return @{ ok = $true }
    } catch {
        return @{ ok = $false; message = $_.Exception.Message }
    }
}

function Select-Python {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = '选择 python.exe（3.9 或更高版本）'
    $dialog.Filter = 'python.exe|python.exe|所有文件 (*.exe)|*.exe'
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        return @{ ok = $false; cancelled = $true }
    }
    $exe = $dialog.FileName
    if (-not (Test-PythonExe $exe)) {
        return @{ ok = $false; message = '这个 python.exe 不可用，需要 Python 3.9 或更高版本。' }
    }
    Save-SavedPython $exe
    Refresh-State
    return @{ ok = $true; path = $exe }
}

function Select-PythonDir {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择 Python 安装目录（官方安装程序会把 Python 装到这里）'
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        return @{ ok = $false; cancelled = $true }
    }
    $path = $dialog.SelectedPath
    if (-not $path) { return @{ ok = $false } }
    $script:InstallDir = $path
    Save-SavedInstallDir $path
    return @{ ok = $true; path = $path }
}

function Start-App {
    if (-not ($script:PythonExe -and $script:DepsOk)) {
        return @{ ok = $false; message = '环境还没有准备好' }
    }
    New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
    $out = Join-Path $script:LogDir 'app.log'
    $err = Join-Path $script:LogDir 'app.err.log'
    try {
        Start-Process -FilePath $script:VenvPython -ArgumentList 'web_app.py' -WorkingDirectory $Root `
            -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
        return @{ ok = $true }
    } catch {
        return @{ ok = $false; message = $_.Exception.Message }
    }
}

function Get-LogTail {
    $parts = @()
    if (Test-Path -LiteralPath $script:DepsLog) {
        $parts += (Get-Content -LiteralPath $script:DepsLog -Tail 200 -Encoding UTF8) -join "`n"
    }
    if (Test-Path -LiteralPath $script:DepsErr) {
        $err = (Get-Content -LiteralPath $script:DepsErr -Tail 80 -Encoding UTF8) -join "`n"
        if ($err.Trim()) { $parts += $err }
    }
    return ($parts -join "`n").Trim()
}

# ---------------- HTTP ----------------

function Get-Status {
    Update-Processes
    $pythonOk = [bool]$script:PythonExe
    $depsMessage = ''
    if (-not $script:DepsOk) {
        $depsMessage = if ($script:PythonExe) { '缺少界面依赖，点击右侧安装' } else { '需要先安装 Python' }
    }
    return @{
        python    = @{ ok = $pythonOk; version = $script:PythonVersion; path = $script:PythonExe }
        deps      = @{ ok = $script:DepsOk; message = $depsMessage }
        tool      = @{
            ok      = [bool]$script:ToolInfo.ok
            source  = $script:ToolInfo.source
            zip     = $script:ToolInfo.zip
            message = $script:ToolInfo.message
            url     = 'https://androidmtk.com/download-amlogic-flash-tool'
        }
        installer = @{
            available   = [bool](Get-Installer)
            downloading = ($script:Task -eq 'python-download')
            progress    = (Get-DownloadProgress)
            error       = $script:DownloadError
            install_dir = $script:InstallDir
            default_dir = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312')
        }
        running   = $script:Task
        ready     = ($pythonOk -and $script:DepsOk)
    }
}

function Send-Bytes($response, [byte[]]$bytes, [string]$contentType) {
    $response.ContentType = $contentType
    $response.Headers['Cache-Control'] = 'no-store'
    $response.ContentLength64 = $bytes.Length
    $response.OutputStream.Write($bytes, 0, $bytes.Length)
}

function Send-Json($response, $payload) {
    $json = $payload | ConvertTo-Json -Depth 8 -Compress
    Send-Bytes $response ([System.Text.Encoding]::UTF8.GetBytes($json)) 'application/json; charset=utf-8'
}

function Send-File($response, [string]$path, [string]$contentType) {
    if (-not (Test-Path -LiteralPath $path)) {
        $response.StatusCode = 404
        Send-Bytes $response ([System.Text.Encoding]::UTF8.GetBytes('not found')) 'text/plain'
        return
    }
    Send-Bytes $response ([System.IO.File]::ReadAllBytes($path)) $contentType
}

function Close-SetupBrowser {
    if ($script:SetupBrowser -and -not $script:SetupBrowser.HasExited) {
        try {
            & taskkill.exe /PID $script:SetupBrowser.Id /T /F 2>$null | Out-Null
        } catch { }
    }
    try {
        Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like '*edge-setup-profile*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
}

function Handle-Request($context) {
    $request = $context.Request
    $response = $context.Response
    $path = $request.Url.AbsolutePath

    switch ($path) {
        '/' { Send-File $response (Join-Path $script:SetupDir 'index.html') 'text/html; charset=utf-8' }
        '/setup.css' { Send-File $response (Join-Path $script:SetupDir 'setup.css') 'text/css; charset=utf-8' }
        '/app.js' { Send-File $response (Join-Path $script:SetupDir 'app.js') 'application/javascript; charset=utf-8' }
        '/style.css' { Send-File $response (Join-Path $script:WebDir 'style.css') 'text/css; charset=utf-8' }
        '/icon.ico' { Send-File $response (Join-Path $script:WebDir 'icon.ico') 'image/x-icon' }
        '/api/status' { Send-Json $response (Get-Status) }
        '/api/refresh' { Refresh-State; Send-Json $response (Get-Status) }
        '/api/log' { Send-Json $response @{ text = (Get-LogTail) } }
        '/api/install-python' { Send-Json $response (Start-PythonInstall) }
        '/api/pick-python' { Send-Json $response (Select-Python) }
        '/api/pick-python-dir' { Send-Json $response (Select-PythonDir) }
        '/api/reset-python-dir' {
            $script:InstallDir = ''
            Save-SavedInstallDir ''
            Send-Json $response @{ ok = $true }
        }
        '/api/install-deps' { Send-Json $response (Start-DepsInstall) }
        '/api/open-tools' {
            $tools = Join-Path $Root 'tools'
            New-Item -ItemType Directory -Force -Path $tools | Out-Null
            Start-Process explorer.exe $tools
            Send-Json $response @{ ok = $true }
        }
        '/api/launch' {
            $result = Start-App
            Send-Json $response $result
            if ($result.ok) {
                Start-Sleep -Milliseconds 700
                $script:Shutdown = $true
                Close-SetupBrowser
            }
        }
        '/api/quit' {
            Send-Json $response @{ ok = $true }
            Close-SetupBrowser
            $script:Shutdown = $true
        }
        default {
            $response.StatusCode = 404
            Send-Bytes $response ([System.Text.Encoding]::UTF8.GetBytes('not found')) 'text/plain'
        }
    }
}

# ---------------- 主流程 ----------------

$script:InstallDir = Get-SavedInstallDir
Refresh-State

if ($Demo) {
    $script:PythonExe = $null
    $script:PythonVersion = ''
    $script:DepsOk = $false
    $script:ToolOk = $false
}

if ($script:PythonExe -and $script:DepsOk -and -not $Setup -and -not $Demo -and -not $ServeOnly) {
    $result = Start-App
    if ($result.ok) { exit 0 }
}

$listener = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $port = Get-Random -Minimum 18000 -Maximum 19999
    $candidate = New-Object System.Net.HttpListener
    $candidate.Prefixes.Add("http://127.0.0.1:$port/")
    try {
        $candidate.Start()
        $listener = $candidate
        $url = "http://127.0.0.1:$port/"
        break
    } catch {
        $candidate.Close()
    }
}

if (-not $listener) {
    [void][System.Windows.Forms.MessageBox]::Show(
        '无法启动本地设置服务（端口被占用或权限不足）。请重启电脑后重试。',
        'Open-XiaoAI 控制台', 'OK', 'Error'
    )
    exit 1
}

$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
Write-Host "初始化页面：$url"
if (-not $ServeOnly) {
    if (Test-Path -LiteralPath $edge) {
        $profile = Join-Path $script:LogDir 'edge-setup-profile'
        New-Item -ItemType Directory -Force -Path $profile | Out-Null
        $script:SetupBrowser = Start-Process -FilePath $edge `
            -ArgumentList "--app=$url", '--window-size=900,700', "--user-data-dir=$profile" -PassThru
    } else {
        Start-Process $url
    }
} else {
    Write-Output "SETUP_URL=$url"
}

try {
    while (-not $script:Shutdown) {
        $context = $listener.GetContext()
        try {
            Handle-Request $context
        } catch {
            try { $context.Response.StatusCode = 500 } catch { }
        } finally {
            try { $context.Response.Close() } catch { }
        }
    }
} finally {
    try { $listener.Stop() } catch { }
    try { $listener.Close() } catch { }
}
