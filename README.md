# Open-XiaoAI Console

小爱音箱**刷机 + 服务端部署**的图形控制台，支持：

- 小爱音箱 Pro（LX06）
- Xiaomi 智能音箱 Pro（OH2P）

> 本项目是 [Open-XiaoAI](https://github.com/idootop/open-xiaoai) 的图形化分支。刷机、部署的核心逻辑来自上游，控制台部分为本分支新增；上游已归档，本分支只维护控制台相关代码。
>
> 控制台目前只支持 **Windows 10 / 11**（依赖系统自带的 WebView2）。

![刷机页](packages/flash-gui-py/docs/screenshot.png)

![部署页](packages/flash-gui-py/docs/screenshot-deploy.png)

## 功能

- **刷机**：官方固件下载 + SHA256 校验、自动抓上电窗口、`bulkcmd` 重试、错误翻译、一键恢复原厂
- **部署**：小智 AI / MiGPT / Gemini Live，支持本机 Docker 或远程服务器（SSH）
- **源码构建**：本地下载源码 → 打包上传 → 服务器解压构建（适配 ARM / 小内存）
- **初始化**：自动检测并安装 Python（支持自定义目录），缺什么装什么
- **状态栏**：服务端端口和音箱在线检测
- **不内置二进制**：Python 安装包和 Amlogic 刷机工具都在首次使用时按提示下载

## 快速开始

1. 克隆或下载本仓库；
2. 双击 `packages/flash-gui-py/启动控制台.cmd`；
3. 首次启动会打开初始化页面，按提示安装 Python 和界面依赖；
4. 把 Amlogic 刷机工具的 zip 放进 `packages/flash-gui-py/tools/`（页面里有官网下载入口）；
5. 回到控制台，选择机型、固件和刷机工具，开始刷机。

详细说明见 [控制台文档](packages/flash-gui-py/README.md)。

## 支持机型

| 机型 | 固件版本 |
|---|---|
| 小爱音箱 Pro（LX06） | 1.94.13 |
| Xiaomi 智能音箱 Pro（OH2P） | 1.58.6 |

其他型号请勿使用，刷错固件有变砖风险。

## 仓库内容

| 目录 | 说明 |
|---|---|
| `packages/flash-gui-py/` | 图形控制台（本分支主要新增内容） |
| `packages/client-rust/`、`packages/client-patch/`、`packages/runtime/` | 音箱端 Client（上游） |
| `examples/` | 上游接入示例：小智 AI、MiGPT、Gemini、KWS、立体声 |
| `docs/` | 上游刷机与使用文档 |

## 免责声明

本项目为开源非营利项目，仅供学术研究或个人测试使用；严禁用于商业服务、网络攻击、数据窃取等违法场景。本项目与小米集团无隶属或合作关系，相关商标、固件、云服务的权利归小米集团所有。

继续使用即表示你已阅读并同意[用户协议](agreement.md)。

## License

MIT License © 2024-PRESENT [Del Wang](https://del.wang)
