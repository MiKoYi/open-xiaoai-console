# Releases 说明

本仓库的 Release 分开提供：控制台、部署包、补丁固件。

| Release | Tag | 内容 |
| --- | --- | --- |
| Windows 控制台 | `v1.0.0` | `OpenXiaoAIConsole.exe` + SHA256 |
| XiaoAI-Plus 部署包 | `v0.1.0` | `oh2p.tar.gz` + SHA256 + 第三方声明 |
| OH2P 1.60.2 补丁固件 | `firmware-oh2p-1.60.2` | `OH2P_1.60.2_patched.squashfs` + SHA256 + 固件声明 |

## 1. Windows 控制台 v1.0.0

- 文件：`OpenXiaoAIConsole.exe`（单文件，Windows 10/11，依赖系统 WebView2）
- 大小：61,583,115 字节
- SHA256：`885e701571ac852ce17995d6e75db893b4de88239e11c2e29b300800421a90a8`
- 内容：控制台源码、网页界面、KWS 模型、Amlogic 刷机工具已内置
- 使用：双击运行；首次运行会检查 Python 依赖（打包版已内置运行环境）
- 许可：[MIT](LICENSE)，第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)

## 2. 部署包 v0.1.0

- 文件：`oh2p.tar.gz`
- SHA256：`3bfa7b55ac8cd2b72e76a6a86a29c3caec95a8fde6ff2d279747d104e32e969d`
- 内容：UPX 压缩的 `xiaoai_plus_speaker`、`boot.sh`、KWS 模型与词表、默认配置模板
- 安装：控制台「部署 → XiaoAI-Plus → 离线安装 / 更新」选择该包，或直接点「安装 / 更新」从本 Release 下载
- 许可：[MIT](LICENSE)，第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)

## 3. OH2P 1.60.2 补丁固件

- 文件：`OH2P_1.60.2_patched.squashfs`
- SHA256：`b235f13be02aa27200f00a67a196570738f1ba984ca8a535a8ffdfbf0b50a9bd`
- 适用：Xiaomi 智能音箱 Pro（OH2P），系统版本 **1.60.2**
- 说明：基于小米官方固件修改的补丁版本
- 风险与许可：补丁固件基于小米官方固件修改，相关权利归小米；仅供学习研究和个人测试，请遵守当地法律及设备用户协议；刷机有风险，后果由使用者自行承担。详见 [docs/firmware-release.md](docs/firmware-release.md)。
