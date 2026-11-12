param([string]$Dest)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$urls = @(
    'https://registry.npmmirror.com/-/binary/python/3.12.7/python-3.12.7-amd64.exe',
    'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
)

foreach ($url in $urls) {
    try {
        Invoke-WebRequest -Uri $url -OutFile $Dest -UseBasicParsing -TimeoutSec 900
        $item = Get-Item -LiteralPath $Dest -ErrorAction Stop
        if ($item.Length -gt 20MB) { exit 0 }
    } catch { }
    Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
}

exit 1
