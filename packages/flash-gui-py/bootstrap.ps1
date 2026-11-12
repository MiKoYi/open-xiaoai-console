param([switch]$Debug)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Add-Type -AssemblyName System.Windows.Forms

$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$BootstrapLog = Join-Path $LogDir 'bootstrap.log'

function Write-Log([string]$Text) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Text
    Add-Content -LiteralPath $BootstrapLog -Value $line -Encoding UTF8
}

function Test-Python([string]$Exe) {
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $version = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $version) { return $false }
        return ([version]$version -ge [version]'3.9')
    } catch {
        return $false
    }
}

function Find-Python {
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
        if (Test-Python $exe) { return $exe }
    }
    return $null
}

Write-Log '========== 启动 =========='
$python = Find-Python
if (-not $python) {
    Write-Log '未检测到 Python 3.9+'
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "未检测到 Python 3.9 或更高版本。`r`n`r`n请先安装 Python，再重新启动控制台。`r`n`r`n是否打开 Python 下载页面？",
        'Open-XiaoAI 控制台',
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
        Start-Process 'https://www.python.org/downloads/windows/'
    }
    exit 1
}
Write-Log "Python：$python"

$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Log '创建虚拟环境 .venv …'
    try {
        & $python -m venv $Venv 2>&1 |
            ForEach-Object { Add-Content -LiteralPath $BootstrapLog -Value $_ -Encoding UTF8 }
    } catch {
        Write-Log "创建虚拟环境失败：$($_.Exception.Message)"
    }
}

$launchPython = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { $python }

function Test-Pywebview([string]$Exe) {
    & $Exe -c "import webview" 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Pywebview $launchPython)) {
    Write-Log '缺少 pywebview，安装窗口依赖…'
    $pip = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input')
    if (-not (Test-Path -LiteralPath $VenvPython)) { $pip += '--user' }
    $pip += @('-i', 'https://pypi.tuna.tsinghua.edu.cn/simple', 'pywebview')
    & $launchPython @pip 2>&1 |
        ForEach-Object { Add-Content -LiteralPath $BootstrapLog -Value $_ -Encoding UTF8 }
    if (-not (Test-Pywebview $launchPython)) {
        Write-Log 'pywebview 安装失败'
        [System.Windows.Forms.MessageBox]::Show(
            "安装窗口依赖 pywebview 失败。`r`n`r`n请检查网络后重试，或手动运行：`r`n$launchPython -m pip install pywebview`r`n`r`n日志：logs\bootstrap.log",
            'Open-XiaoAI 控制台',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        )
        exit 1
    }
    Write-Log 'pywebview 安装完成'
}

Write-Log "启动：$launchPython web_app.py"

$appOut = Join-Path $LogDir 'app.log'
$appErr = Join-Path $LogDir 'app.err.log'
try {
    Start-Process -FilePath $launchPython -ArgumentList 'web_app.py' -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $appOut -RedirectStandardError $appErr | Out-Null
} catch {
    Write-Log "启动失败：$($_.Exception.Message)"
    [System.Windows.Forms.MessageBox]::Show(
        "启动失败：$($_.Exception.Message)`r`n`r`n详细信息见 logs\bootstrap.log。",
        'Open-XiaoAI 控制台',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    )
    exit 1
}
