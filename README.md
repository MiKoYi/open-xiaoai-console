> [!WARNING]
> 刷机有风险，变砖自负。仅支持 LX06 / OH2P，**小白不建议尝试**，需要一定的动手能力和排错能力；请自行备份并承担后果。

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
| `examples/` | 部署模板和 Dockerfile，**部署页依赖，勿删** |
| `packages/client-rust/` | 音箱端 Client，部署构建会 `COPY` 它，**勿删** |
| `packages/client-patch/`、`packages/runtime/` | 固件构建与运行环境（上游） |
| `docs/` | 刷机与使用文档 |

## 免责声明

1. **适用范围**
   本项目为开源非营利项目，仅供学术研究或个人测试用途。严禁用于商业服务、网络攻击、数据窃取、系统破坏等违反《网络安全法》及使用者所在地司法管辖区的法律规定的场景。
2. **非官方声明**
   本项目由第三方开发者独立开发，与小米集团及其关联方（下称"权利方"）无任何隶属/合作关系，亦未获其官方授权/认可或技术支持。项目中涉及的商标、固件、云服务的所有权利归属小米集团。若权利方主张权益，使用者应立即主动停止使用并删除本项目。

继续下载或运行本项目，即表示您已完整阅读并同意[用户协议](agreement.md)，否则请立即终止使用并彻底删除本项目。

## License

MIT License © 2024-PRESENT [Del Wang](https://del.wang)
