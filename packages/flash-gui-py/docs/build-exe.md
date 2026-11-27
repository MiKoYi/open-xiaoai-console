# 打包 Windows 单文件控制台

需要 Windows 10/11、Python 3.12（或 3.9+）和 WebView2。

```powershell
cd packages\flash-gui-py
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
```

在 `packages\flash-gui-py` 下执行：

```powershell
$root = (Get-Location).Path
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name OpenXiaoAIConsole `
  --icon web/icon.ico `
  --version-file version_info.txt `
  --add-data "$root\web;web" `
  --add-data "$root\assets;assets" `
  --add-data "$root\patcher;patcher" `
  --add-data "$root\tools;tools" `
  --collect-all webview --collect-submodules webview `
  --hidden-import=clr --hidden-import=pythonnet --hidden-import=paramiko `
  --hidden-import=flash_worker `
  --distpath dist --workpath build-final web_app.py
```

产物：`dist\OpenXiaoAIConsole.exe`。版本号来自 `version_info.txt`（ProductVersion/FileVersion = 1.0.0）。

验证：

```powershell
.\dist\OpenXiaoAIConsole.exe --selftest
```

看到 `SELFTEST OK` 即表示内置服务、网页资源和依赖正常。双击运行会打开 WebView2 窗口；未签名的 exe 在 Windows 上可能触发 SmartScreen 提示，选择「更多信息 → 仍要运行」即可。
