# 把官方固件制作成补丁固件

> [!WARNING]
> 刷机有风险，变砖自负。补丁固件只适用于同型号、同版本的官方固件。

## 两种固件

Release 里通常有两个文件：

| 文件 | 用途 |
|---|---|
| `LX06_1.94.13.squashfs` | 官方原版固件，可以刷回原系统，也可以自己打补丁 |
| `LX06_1.94.13_patched.squashfs` | 已经打好的补丁固件，开启 SSH、禁用自动更新 |

控制台里对应三个按钮：

- **下载补丁固件**：直接下载 `*_patched.squashfs`；
- **下载官方固件**：下载官方原版固件；
- **制作补丁固件**：把当前选中的官方固件制作成补丁固件。

## 方式一：控制台自动制作（推荐）

1. 先点「下载官方固件」，或「浏览…」选择本地官方固件；
2. 点「制作补丁固件」；
3. 本机有 Docker 就直接用本机；
4. 本机没有 Docker 时，可以选择远程服务器（需要 SSH，服务器已安装 Docker）；
5. 两边都没有，按下面的手动步骤操作。

制作完成后会自动选中新生成的 `*_patched.squashfs`。

## 方式二：手动 Docker

需要：

- Docker（Windows / macOS 安装 Docker Desktop）；
- 本仓库的 `packages/client-patch/patches`；
- 本仓库的 `packages/flash-gui-py/patcher/patch.sh`；
- 官方固件 `.squashfs`。

在仓库根目录执行（把路径换成你的实际路径）：

```bash
docker run --rm --platform linux/amd64 \
  -v "/path/to/official:/in:ro" \
  -v "/path/to/output:/out" \
  -v "$(pwd)/packages/client-patch/patches:/patches:ro" \
  -v "$(pwd)/packages/flash-gui-py/patcher/patch.sh:/patch.sh:ro" \
  -e MODEL=LX06 \
  -e IN_FILE=LX06_1.94.13.squashfs \
  -e OUT_FILE=LX06_1.94.13_patched.squashfs \
  -e SSH_PASSWORD=open-xiaoai \
  idootop/open-xiaoai:latest \
  bash /patch.sh
```

Windows PowerShell 里路径要用正斜杠，例如 `D:/firmware`。

参数说明：

| 变量 | 说明 |
|---|---|
| `MODEL` | `LX06` 或 `OH2P` |
| `IN_FILE` | `/in` 目录里的官方固件文件名 |
| `OUT_FILE` | 输出到 `/out` 的文件名 |
| `SSH_PASSWORD` | 补丁固件开启 SSH 后的登录密码，默认 `open-xiaoai` |

## 方式三：本地 Linux 构建

按照上游 `packages/client-patch/README.md` 的「本地构建」步骤，需要 Python、Node.js 和 squashfs 工具。

## 常见问题

- **Docker 拉取镜像很慢**：先配置 Docker 镜像加速，或换一台能访问 Docker Hub 的服务器；
- **提示文件不完整**：确认下载的是 `.squashfs`，不是网页或压缩包；
- **生成的固件超过 40MB**：说明补丁或原固件不匹配，换回官方原版固件重新制作；
- **刷入后没有 SSH**：确认刷的是 `*_patched.squashfs`，不是官方原版。
