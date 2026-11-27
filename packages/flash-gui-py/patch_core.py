"""官方固件补丁：用 Docker 在本机或远程服务器运行 client-patch。"""

from __future__ import annotations

import re
import tarfile
import tempfile
from pathlib import Path

import deploy_core as dcore
import flash_core as core

ROOT = Path(__file__).resolve().parent
PATCHES_DIR = ROOT.parent / "client-patch" / "patches"
PATCH_SCRIPT = ROOT / "patcher" / "patch.sh"
DOCKER_IMAGE = "idootop/open-xiaoai:latest"
GUIDE_URL = "https://github.com/idootop/open-xiaoai/blob/main/packages/client-patch/README.md"


class PatchError(core.FlashError):
    """可以直接展示给用户的制作失败原因。"""


def docker_status() -> tuple[bool, str]:
    return dcore.check_docker()


def _log(notify, text: str, level: str = "info") -> None:
    if notify:
        notify("log", text, level)


def _docker_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _output_version(image: Path, model: str) -> str:
    """输出文件名沿用输入固件的版本号（不再写死控制台内置的版本）。"""
    match = re.search(r"(\d+\.\d+\.\d+)", image.name)
    return match.group(1) if match else core.get_model_info(model).version


def _output_path(model: str, version: str | None = None) -> Path:
    info = core.get_model_info(model)
    return ROOT / "firmware" / f"{info.model}_{version or info.version}_patched.squashfs"


def _check_input(image: Path, model: str, notify) -> None:
    check = core.test_firmware(str(image), model)
    if not check.ok:
        raise PatchError(check.error)
    for warning in check.warnings:
        if "_patched" in warning:
            continue
        _log(notify, warning, "info")


def _verify_output(path: Path, model: str) -> None:
    check = core.test_firmware(str(path), model)
    if not check.ok:
        path.unlink(missing_ok=True)
        raise PatchError(f"生成的固件校验失败：{check.error}")


def _tail(output: str, lines: int = 8) -> str:
    rows = [row for row in (output or "").splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def _patch_local(image: Path, model: str, out: Path, notify) -> None:
    ok, message = dcore.check_docker()
    if not ok:
        raise PatchError(f"本机 Docker 不可用：{message}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    _log(notify, f"使用本机 Docker 制作补丁固件（{DOCKER_IMAGE}）…")
    args = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-v",
        f"{_docker_path(image.parent)}:/in:ro",
        "-v",
        f"{_docker_path(out.parent)}:/out",
        "-v",
        f"{_docker_path(PATCHES_DIR)}:/patches:ro",
        "-v",
        f"{_docker_path(PATCH_SCRIPT)}:/patch.sh:ro",
        "-e",
        f"MODEL={model}",
        "-e",
        f"IN_FILE={image.name}",
        "-e",
        f"OUT_FILE={out.name}",
        "-e",
        "SSH_PASSWORD=open-xiaoai",
        DOCKER_IMAGE,
        "bash",
        "/patch.sh",
    ]
    code, output = dcore._stream(args, notify)
    if code != 0:
        raise PatchError("Docker 制作补丁固件失败：\n" + _tail(output))
    if not out.is_file():
        raise PatchError("Docker 执行完成，但没有生成补丁固件，请查看日志。")


def _remote_arch(client) -> str:
    code, output = dcore._ssh_run(client, "uname -m", quiet=True)
    if code != 0:
        return ""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _patch_remote(image: Path, model: str, out: Path, ssh, notify) -> None:
    client = dcore._ssh_connect(ssh)
    try:
        code, output = dcore._ssh_run(client, "docker info --format '{{.ServerVersion}}'", notify)
        if code != 0:
            raise PatchError("远程服务器 Docker 不可用，请确认已安装并启动 Docker。")

        arch = _remote_arch(client)
        if arch:
            _log(notify, f"服务器架构：{arch}")
            if arch.lower() in ("aarch64", "arm64", "armv7l", "armv8l"):
                _log(
                    notify,
                    "⚠️ 补丁镜像是 linux/amd64，ARM 服务器需要 QEMU 模拟（会慢一些）。"
                    "如果报 exec format error，先在服务器执行：\n"
                    "  docker run --privileged --rm tonistiigi/binfmt --install amd64",
                    "warn",
                )

        remote_dir = f"{ssh.home}/open-xiaoai/patch"
        dcore._ssh_run(client, f"mkdir -p '{remote_dir}/in' '{remote_dir}/out'", notify)

        _log(notify, "正在上传官方固件和补丁脚本…")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "patch.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(image, arcname=f"in/{image.name}")
                tar.add(PATCHES_DIR, arcname="patches")
                tar.add(PATCH_SCRIPT, arcname="patch.sh")
            dcore._upload_file(client, archive, f"{remote_dir}/patch.tar.gz", notify)

        code, output = dcore._ssh_run(client, f"cd '{remote_dir}' && tar -xzf patch.tar.gz", notify)
        if code != 0:
            raise PatchError("远程解压补丁资源失败：\n" + _tail(output))

        command = (
            f"cd '{remote_dir}' && docker run --rm --platform linux/amd64 "
            f"-v \"$PWD/in:/in:ro\" -v \"$PWD/out:/out\" "
            f"-v \"$PWD/patches:/patches:ro\" -v \"$PWD/patch.sh:/patch.sh:ro\" "
            f"-e MODEL={model} -e IN_FILE='{image.name}' -e OUT_FILE='{out.name}' "
            f"-e SSH_PASSWORD=open-xiaoai {DOCKER_IMAGE} bash /patch.sh"
        )
        code, output = dcore._ssh_run(client, command, notify)
        if code != 0:
            lowered = output.lower()
            if "exec format error" in lowered:
                raise PatchError(
                    "服务器是 ARM，补丁镜像（linux/amd64）需要 QEMU 模拟。先在服务器执行：\n"
                    "  docker run --privileged --rm tonistiigi/binfmt --install amd64\n"
                    "然后重试；不想用模拟就在服务器上装 squashfs-tools 原生打补丁。"
                )
            if "unauthorized" in lowered or "denied" in lowered or "no such host" in lowered:
                raise PatchError(
                    "服务器拉取补丁镜像失败（网络 / 未登录 Docker Hub）：\n" + _tail(output)
                    + "\n可以给服务器配置 Docker 镜像加速或代理后重试。"
                )
            raise PatchError("远程 Docker 制作补丁固件失败：\n" + _tail(output))

        _log(notify, "正在下载生成的补丁固件…")
        dcore._download_file(client, f"{remote_dir}/out/{out.name}", out, notify)
        if not out.is_file():
            raise PatchError("远程制作完成，但下载补丁固件失败。")
    finally:
        client.close()


def patch_official(
    image: str | Path,
    model: str = "LX06",
    target: str = "local",
    ssh=None,
    notify=None,
) -> Path:
    """把官方固件制作成补丁固件。

    target = local（本机 Docker）或 remote（远程服务器 Docker，需要 ssh）。
    """
    source = Path(image)
    if not source.is_file():
        raise PatchError(f"官方固件不存在：{source}")
    if "_patched" in source.name.lower():
        raise PatchError("当前选择的是补丁固件，请选择官方固件（文件名不含 _patched）再制作。")
    _check_input(source, model, notify)

    out = _output_path(model, _output_version(source, model))
    out.parent.mkdir(parents=True, exist_ok=True)

    if target == "remote":
        if ssh is None:
            raise PatchError("远程制作补丁固件需要 SSH 配置。")
        _log(notify, f"使用远程服务器 {ssh.host} 制作补丁固件…")
        _patch_remote(source, model, out, ssh, notify)
    else:
        _patch_local(source, model, out, notify)

    if not out.is_file():
        raise PatchError("没有生成补丁固件，请查看日志。")
    _verify_output(out, model)
    _log(notify, f"补丁固件制作完成：{out}", "ok")
    return out
