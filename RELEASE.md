# Releases 说明

本仓库的 Release 分两次发布，**固件和部署包分开**：

| Release | Tag | 内容 |
| --- | --- | --- |
| XiaoAI-Plus 部署包 | `v0.1.0` | `oh2p.tar.gz` + SHA256 + 第三方声明 |
| OH2P 1.60.2 补丁固件 | `firmware-oh2p-1.60.2` | `OH2P_1.60.2_patched.squashfs` + SHA256 + 固件声明 |

**不发布官方原版固件**（`OH2P_1.60.2.squashfs`），官方固件请自行从小米 OTA 获取。

## 1. 部署包 v0.1.0

- 文件：`oh2p.tar.gz`
- SHA256：`3bfa7b55ac8cd2b72e76a6a86a29c3caec95a8fde6ff2d279747d104e32e969d`
- 内容：UPX 压缩的 `xiaoai_plus_speaker`、`boot.sh`、KWS 模型与词表、默认配置模板
- 安装：控制台「部署 → XiaoAI-Plus → 离线安装 / 更新」选择该包，或直接点「安装 / 更新」从本 Release 下载
- 许可：[MIT](LICENSE)，第三方组件见 [THIRD_PARTY.md](THIRD_PARTY.md)

## 2. OH2P 1.60.2 补丁固件

- 文件：`OH2P_1.60.2_patched.squashfs`
- SHA256：`b235f13be02aa27200f00a67a196570738f1ba984ca8a535a8ffdfbf0b50a9bd`
- 适用：Xiaomi 智能音箱 Pro（OH2P），系统版本 **1.60.2**
- 说明：基于小米官方固件修改，**仅补丁版，不含官方原版**
- 风险与许可：见 [docs/firmware-release.md](docs/firmware-release.md)。补丁固件版权归小米，仅限已拥有该设备的用户个人刷机使用，请勿商用或再分发；刷机有变砖风险。
