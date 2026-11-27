"""Open-XiaoAI 服务端部署核心逻辑。

根据仓库 examples/ 下的文档，把「大模型接入方式」抽象成可部署的模式：
- 小智 AI：官方 Docker 镜像 + config.py
- MiGPT：官方 Docker 镜像 + config.ts

支持部署到本机 Docker 或远程服务器（SSH）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_TARBALL_PATH = "https://codeload.github.com/idootop/open-xiaoai/tar.gz/refs/heads/main"
MIGPT_LEAN_DOCKERFILE = REPO_ROOT / "examples" / "migpt" / "Dockerfile.lean"
DEFAULT_PORT = 4399
CONTAINER_PREFIX = "open-xiaoai"
# 小内存机器编译镜像前可以先加 2G swap，失败提示和内存警告共用这段命令
SWAP_COMMAND = (
    "sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && "
    "sudo mkswap /swapfile && sudo swapon /swapfile"
)

# 国内常见的 GitHub 加速前缀；值为空表示直连
MIRROR_PRESETS = {
    "auto": ("自动（直连 → ghfast.top → ghproxy.net）", ""),
    "github": ("GitHub 直连", ""),
    "ghfast": ("ghfast.top", "https://ghfast.top/"),
    "ghproxy": ("ghproxy.net", "https://ghproxy.net/"),
    "gitmirror": ("hub.gitmirror.com", "https://hub.gitmirror.com/"),
    "custom": ("自定义前缀", ""),
}


def mirror_candidates(mirror: str = "auto", custom_prefix: str = "") -> list[tuple[str, str]]:
    """返回 [(显示名, 前缀), ...]，按顺序尝试。"""
    if mirror == "custom":
        prefix = (custom_prefix or "").strip()
        if not prefix:
            raise DeployError("请填写自定义加速前缀，例如 https://ghfast.top/")
        if not prefix.endswith("/"):
            prefix += "/"
        return [("自定义", prefix)]

    if mirror == "auto":
        return [
            ("GitHub 直连", ""),
            ("ghfast.top", "https://ghfast.top/"),
            ("ghproxy.net", "https://ghproxy.net/"),
        ]

    label, prefix = MIRROR_PRESETS.get(mirror, MIRROR_PRESETS["github"])
    return [(label, prefix)]


def tarball_url(prefix: str = "") -> str:
    return prefix + REPO_TARBALL_PATH


def apply_image_prefix(image: str, prefix: str) -> str:
    """给官方镜像加加速前缀，例如 docker.m.daocloud.io/。"""
    value = (prefix or "").strip()
    if not value:
        return image
    if not value.endswith("/"):
        value += "/"
    return value + image


class DeployError(Exception):
    """可以直接展示给用户的错误。"""


Notify = Optional[Callable[[str, str, str], None]]


def _log(notify: Notify, text: str, level: str = "info") -> None:
    if notify:
        notify("log", text, level)


def _status(notify: Notify, text: str) -> None:
    if notify:
        notify("status", text, "")


# ---------------- 配置字段 ----------------


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    default: str = ""
    required: bool = False
    secret: bool = False
    multiline: bool = False
    help: str = ""
    placeholder: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModeSpec:
    key: str
    title: str
    tagline: str
    kind: str  # docker | source
    doc: str
    image: str = ""
    config_name: str = ""
    fields: tuple[Field, ...] = ()
    note: str = ""

    @property
    def deployable(self) -> bool:
        return self.kind == "docker"


XIAOZHI = ModeSpec(
    key="xiaozhi",
    title="小智 AI",
    tagline="官方服务端，支持连续对话和唤醒词",
    kind="docker",
    doc="examples/xiaozhi/README.md",
    image="idootop/open-xiaoai-xiaozhi:latest",
    config_name="config.py",
    fields=(
        Field(
            key="keywords",
            label="自定义唤醒词（用逗号分隔，英文要小写）",
            default="你好小智,豆包豆包,天猫精灵",
            required=True,
        ),
        Field(
            key="ota_url",
            label="OTA 地址",
            default="https://api.tenclass.net/xiaozhi/ota/",
            required=True,
        ),
        Field(
            key="ws_url",
            label="WebSocket 地址",
            default="wss://api.tenclass.net/xiaozhi/v1/",
            required=True,
        ),
    ),
    note="部署后首次对话需要在 xiaozhi.me 绑定设备，验证码会显示在容器日志里。",
)

MIGPT = ModeSpec(
    key="migpt",
    title="MiGPT",
    tagline="兼容 OpenAI 接口的大模型",
    kind="docker",
    doc="examples/migpt/README.md",
    image="idootop/open-xiaoai-migpt:latest",
    config_name="config.ts",
    fields=(
        Field(key="api_key", label="API Key", required=True, secret=True, placeholder="sk-..."),
        Field(
            key="base_url",
            label="接口地址（OpenAI 兼容地址，通常以 /v1 结尾）",
            default="https://ark.cn-beijing.volces.com/api/v3",
            required=True,
            options=(
                "https://ark.cn-beijing.volces.com/api/v3",
                "https://api.deepseek.com",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "https://api.moonshot.cn/v1",
                "https://open.bigmodel.cn/api/paas/v4",
                "https://api.siliconflow.cn/v1",
                "https://api.openai.com/v1",
            ),
        ),
        Field(
            key="model",
            label="模型名称",
            default="doubao-seed-1-6-250615",
            required=True,
            options=(
                "doubao-seed-1-6-250615",
                "doubao-seed-1-6-flash-250715",
                "doubao-1-5-pro-32k-250115",
                "doubao-1-5-lite-32k-250115",
                "deepseek-flash",
                "deepseek-v4-pro",
                "qwen-plus",
                "qwen-max",
                "qwen-turbo",
                "moonshot-v1-8k",
                "moonshot-v1-32k",
                "glm-4-plus",
                "glm-4-flash",
                "gpt-4.1-mini",
                "gpt-4.1",
                "gpt-4o-mini",
                "gpt-4o",
            ),
        ),
        Field(
            key="system_prompt",
            label="系统提示词",
            default="你是一个智能助手，请根据用户的问题给出回答。",
            multiline=True,
        ),
    ),
    note="默认使用豆包（火山方舟 Ark）；也支持 DeepSeek、通义、Moonshot 等任何兼容 OpenAI 接口的服务。",
)

MODES: dict[str, ModeSpec] = {spec.key: spec for spec in (XIAOZHI, MIGPT)}


def get_mode(key: str) -> ModeSpec:
    try:
        return MODES[key]
    except KeyError as exc:
        raise DeployError(f"未知的接入方式：{key}") from exc


# ---------------- 文档发现 ----------------


@dataclass(frozen=True)
class ExampleEntry:
    key: str
    title: str
    doc_path: Path
    summary: str
    spec: Optional[ModeSpec] = None

    @property
    def deployable(self) -> bool:
        return self.spec is not None and self.spec.deployable


def _read_title_and_summary(readme: Path) -> tuple[str, str]:
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return readme.parent.name, ""

    title = readme.parent.name
    summary = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# ") and title == readme.parent.name:
            title = line[2:].strip()
            continue
        if not line or line.startswith(("#", ">", "!", "-", "|", "```")):
            continue
        if title != readme.parent.name and not summary:
            summary = line
            break
    return title, summary


def discover_examples(repo_root: Path | str = REPO_ROOT) -> list[ExampleEntry]:
    """按 examples/ 下的 README 生成可选列表（顺序固定，便于界面展示）。"""
    root = Path(repo_root) / "examples"
    order = ["xiaozhi", "migpt", "kws", "stereo"]
    found: dict[str, ExampleEntry] = {}

    if root.is_dir():
        for readme in root.glob("*/README.md"):
            key = readme.parent.name
            title, summary = _read_title_and_summary(readme)
            found[key] = ExampleEntry(key=key, title=title, doc_path=readme, summary=summary, spec=MODES.get(key))

    entries: list[ExampleEntry] = []
    for key in order:
        if key in found:
            entries.append(found.pop(key))
    entries.extend(found.values())
    return entries


# ---------------- 表单校验 ----------------


def validate(mode_key: str, values: dict[str, str]) -> list[str]:
    spec = get_mode(mode_key)
    errors: list[str] = []

    for field in spec.fields:
        value = (values.get(field.key) or "").strip()
        if field.required and not value:
            errors.append(f"请填写「{field.label}」")
        if value and field.key.endswith("_url") and not re.match(r"^(https?|wss?)://", value):
            errors.append(f"「{field.label}」需要以 http(s):// 或 ws(s):// 开头")

    if spec.key == "xiaozhi":
        keywords = [item.strip() for item in (values.get("keywords") or "").split(",") if item.strip()]
        if not keywords:
            errors.append("至少填写一个唤醒词")
        elif any(len(item) > 20 for item in keywords):
            errors.append("唤醒词太长了，建议不超过 20 个字符")

    if spec.key == "migpt":
        base_url = (values.get("base_url") or "").strip()
        if base_url and base_url.endswith("/chat/completions"):
            errors.append("接口地址不要带 /chat/completions，一般以 /v1 结尾")

    return errors


# ---------------- 配置文件生成 ----------------


def _q(value: str) -> str:
    """生成同时适用于 Python 和 TypeScript 的字符串字面量。"""
    return json.dumps(value, ensure_ascii=False)


def render_config(mode_key: str, values: dict[str, str]) -> str:
    spec = get_mode(mode_key)

    if spec.key == "xiaozhi":
        keywords = [item.strip() for item in (values.get("keywords") or "").split(",") if item.strip()]
        keyword_lines = ",\n".join(f"            {_q(item)}" for item in keywords)
        return f'''import asyncio


async def before_wakeup(speaker, text, source):
    """收到唤醒词 / 小爱指令时的处理。"""
    if source == "kws":
        await speaker.play(text="你好主人，请问有什么吩咐？")
        return True

    if source == "xiaoai" and text == "召唤小智":
        await speaker.abort_xiaoai()
        await asyncio.sleep(2)
        await speaker.play(text="小智来了，主人有什么吩咐？")
        return True


async def after_wakeup(speaker):
    await speaker.play(text="主人再见，拜拜")


APP_CONFIG = {{
    "wakeup": {{
        "keywords": [
{keyword_lines}
        ],
        "timeout": 20,
        "before_wakeup": before_wakeup,
        "after_wakeup": after_wakeup,
    }},
    "vad": {{
        "threshold": 0.10,
        "min_speech_duration": 250,
        "min_silence_duration": 500,
    }},
    "xiaozhi": {{
        "OTA_URL": {_q(values.get("ota_url", ""))},
        "WEBSOCKET_URL": {_q(values.get("ws_url", ""))},
        "WEBSOCKET_ACCESS_TOKEN": "",
        "VERIFICATION_CODE": "",
        "DEVICE_ID": "",
    }},
}}
'''

    if spec.key == "migpt":
        return f'''import {{ OpenXiaoAIConfig }} from "./migpt/xiaoai.js";

export const kOpenXiaoAIConfig: OpenXiaoAIConfig = {{
  openai: {{
    baseURL: {_q(values.get("base_url", ""))},
    apiKey: {_q(values.get("api_key", ""))},
    model: {_q(values.get("model", ""))},
  }},
  prompt: {{
    system: {_q(values.get("system_prompt", ""))},
  }},
  context: {{
    historyMaxLength: 10,
  }},
  callAIKeywords: ["请", "你"],
  async onMessage(engine, {{ text }}) {{
    if (text === "测试播放文字") {{
      return {{ text: "你好，很高兴认识你！" }};
    }}
  }},
}};
'''

    raise DeployError(f"暂不支持生成 {spec.title} 的配置")


def config_filename(mode_key: str) -> str:
    return get_mode(mode_key).config_name


def write_config(mode_key: str, values: dict[str, str], base_dir: Path | str) -> Path:
    spec = get_mode(mode_key)
    if not spec.config_name:
        raise DeployError(f"{spec.title} 暂不支持生成配置文件")

    directory = Path(base_dir) / spec.key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / spec.config_name
    path.write_text(render_config(mode_key, values), encoding="utf-8")
    return path


# ---------------- 本机 / 远程 ----------------


def container_name(mode_key: str) -> str:
    return f"{CONTAINER_PREFIX}-{mode_key}"


def docker_run_args(
    mode_key: str,
    config_path: Path | str,
    host_port: int = DEFAULT_PORT,
    memory: str = "",
    image: str = "",
    source_build: bool = False,
) -> list[str]:
    spec = get_mode(mode_key)
    if not spec.deployable:
        raise DeployError(f"{spec.title} 暂不支持 Docker 部署")
    container = container_name(mode_key)
    args = [
        "run",
        "-d",
        "--name",
        container,
        "--restart",
        "unless-stopped",
        "-p",
        f"{host_port}:{DEFAULT_PORT}",
        "-v",
        f"{config_path}:/app/{spec.config_name}",
    ]
    if memory:
        args += ["--memory", memory, "--memory-swap", _memory_swap(memory)]
    if source_build and mode_key == "migpt":
        heap = _node_heap_mb(memory)
        if heap:
            args += ["-e", f"NODE_OPTIONS=--max-old-space-size={heap} --max-semi-space-size=8"]
    args.append(image or spec.image)
    return args


def validate_memory(memory: str) -> str:
    """校验容器内存上限，返回错误信息；空字符串表示不限制。"""
    value = (memory or "").strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d+)([mMgG])", value)
    if not match or int(match.group(1)) <= 0:
        return "内存上限格式不对，请填写类似 512m、1g 的值，留空表示不限制。"
    return ""


def _memory_swap(memory: str) -> str:
    match = re.fullmatch(r"(\d+)([mMgG])", memory.strip())
    if not match:
        return memory
    amount = int(match.group(1)) * 2
    unit = "m" if match.group(2).lower() == "m" else "g"
    return f"{amount}{unit}"


def _memory_mb(memory: str) -> int | None:
    match = re.fullmatch(r"(\d+)([mMgG])", (memory or "").strip())
    if not match:
        return None
    value = int(match.group(1))
    return value if match.group(2).lower() == "m" else value * 1024


def _node_heap_mb(memory: str) -> int | None:
    limit = _memory_mb(memory)
    if not limit:
        return None
    return max(64, min(384, limit // 2))


def source_image_tag(mode_key: str) -> str:
    return f"open-xiaoai-{mode_key}:local"


def source_repo_dir(base_dir: Path | str) -> Path:
    return Path(base_dir) / "source" / "open-xiaoai"


def _prepare_source_tree(mode_key: str, repo_dir: Path | str) -> None:
    """把仓库里的精简 Dockerfile 放进下载的源码目录。"""
    if mode_key != "migpt" or not MIGPT_LEAN_DOCKERFILE.is_file():
        return
    target = Path(repo_dir) / "examples" / "migpt" / "Dockerfile.lean"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = MIGPT_LEAN_DOCKERFILE.read_bytes()
    # 内容没变就不重写，保持 mtime 稳定：源码包的 sha256 才不会被无谓地改变
    if not target.is_file() or target.read_bytes() != data:
        target.write_bytes(data)


def _proxy_build_args(build_proxy: str) -> list[str]:
    """构建时走代理：docker 支持预定义 build-arg，不用改 Dockerfile。

    127.0.0.1 / localhost 会自动换成 host.docker.internal，并加 --add-host，
    否则构建容器里连不到宿主机上的代理。
    """
    proxy = (build_proxy or "").strip()
    if not proxy:
        return []
    mapped = _container_proxy(proxy)
    args: list[str] = []
    if mapped != proxy:
        args += ["--add-host", "host.docker.internal:host-gateway"]
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        args += ["--build-arg", f"{name}={mapped}"]
    args += ["--build-arg", "NO_PROXY=localhost,127.0.0.1,host.docker.internal"]
    return args


def _proxy_log_text(proxy: str) -> str:
    value = proxy.strip()
    mapped = _container_proxy(value)
    if mapped != value:
        return f"构建代理：{value}（容器内改用 {mapped}）"
    return f"构建代理：{value}"


def docker_build_args(mode_key: str, repo_dir: Path | str, build_proxy: str = "") -> list[str]:
    repo = Path(repo_dir)
    dockerfile = repo / "examples" / mode_key / "Dockerfile"
    if mode_key == "migpt":
        lean = repo / "examples" / "migpt" / "Dockerfile.lean"
        if lean.is_file():
            dockerfile = lean
    return [
        "build",
        "-f",
        str(dockerfile),
        "-t",
        source_image_tag(mode_key),
        *_proxy_build_args(build_proxy),
        str(repo),
    ]


def connection_url(host: str, port: int = DEFAULT_PORT) -> str:
    return f"ws://{host}:{port}"


def tcp_ok(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def speaker_online(ip: str) -> bool:
    """先试 SSH 端口，再用 ICMP 兜底（有些网络封了 22 但能 ping 通）。"""
    try:
        with socket.create_connection((ip, 22), timeout=1.2):
            return True
    except OSError:
        pass

    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
        completed = subprocess.run(
            ["ping", "-n", "1", "-w", "1200", ip],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=flags,
        )
        return "TTL=" in (completed.stdout or "").upper()
    except (OSError, subprocess.SubprocessError):
        return False


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _creation_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def _stream(command: list[str], notify: Notify = None, cwd: Path | str | None = None) -> tuple[int, str]:
    """执行命令并逐行回调输出。"""
    _log(notify, "$ " + " ".join(str(part) for part in command))
    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        raise DeployError(f"无法执行命令：{exc}") from exc

    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        lines.append(line)
        if line:
            _log(notify, line)
    process.wait()
    return process.returncode, "\n".join(lines)


def check_docker() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "没有找到 docker 命令"
    code, output = _stream(["docker", "info", "--format", "{{.ServerVersion}}"])
    if code != 0:
        return False, "Docker 已安装，但服务没有运行"
    return True, output.strip().splitlines()[-1] if output.strip() else "已运行"


def detect_local_env(notify: Notify = None) -> tuple[str, float] | None:
    """返回本机 Docker 的架构和内存（GB）。"""
    code, output = _stream(["docker", "info", "--format", "{{.Architecture}} {{.MemTotal}}"])
    if code != 0:
        return None
    parts = output.strip().split()
    if len(parts) < 2:
        return None
    try:
        memory_gb = int(parts[1]) / (1024 ** 3)
    except ValueError:
        return None
    _log(notify, f"本机 Docker：架构 {parts[0]}，内存 {memory_gb:.1f} GB")
    return parts[0], memory_gb


def _source_cache_ready(repo: Path) -> bool:
    """本机源码缓存是否可用：完整的下载会写 .source-complete 标记。"""
    if not (repo / "examples").is_dir():
        return False
    if (repo / ".source-complete").is_file():
        return True
    # 兼容旧版本下载的缓存：至少要有一个能直接构建的 Dockerfile，避免用到解压到一半的目录
    try:
        return any(
            (child / "Dockerfile").is_file()
            for child in (repo / "examples").iterdir()
            if child.is_dir()
        )
    except OSError:
        return False


def fetch_source_bundle(
    base_dir: Path | str,
    mirror: str = "auto",
    custom_prefix: str = "",
    notify: Notify = None,
) -> Path:
    """在本机准备源码：有缓存直接用，没有就按加速源下载压缩包。"""
    repo = source_repo_dir(base_dir)

    if _source_cache_ready(repo):
        _log(notify, f"使用本机已缓存的源码：{repo}")
        return repo

    repo.parent.mkdir(parents=True, exist_ok=True)
    candidates = mirror_candidates(mirror, custom_prefix)
    errors: list[str] = []

    for label, prefix in candidates:
        _log(notify, f"正在从 {label} 下载源码…")
        try:
            download_source_tarball(repo, tarball_url(prefix), label, notify)
            _log(notify, f"源码已就绪（来源：{label}）", "ok")
            return repo
        except DeployError as exc:
            errors.append(f"{label}：{exc}")
            shutil.rmtree(repo, ignore_errors=True)
            _log(notify, f"{label} 下载失败，尝试下一个源…", "warn")

    raise DeployError(
        "源码下载失败，已尝试：\n- " + "\n- ".join(errors)
        + "\n可以在「选项」里换一个下载源，或填写自定义加速前缀（如 https://ghfast.top/）。"
    )


def download_source_tarball(repo: Path, url: str, label: str = "", notify: Notify = None) -> None:
    """下载源码压缩包并解压到 repo。"""
    repo.parent.mkdir(parents=True, exist_ok=True)
    archive = repo.parent / "open-xiaoai-src.tar.gz"

    request = Request(url, headers={"User-Agent": "open-xiaoai-console"})
    try:
        with urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_percent = -1
            with archive.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = downloaded * 100 // total
                        if percent >= last_percent + 10:
                            last_percent = percent
                            _log(notify, f"下载源码… {percent}%")
                            _status(notify, f"正在下载源码… {percent}%")
    except Exception as exc:  # noqa: BLE001 - 网络错误类型很多
        archive.unlink(missing_ok=True)
        raise DeployError(f"下载失败（{exc}）") from exc

    if archive.stat().st_size < 10 * 1024:
        archive.unlink(missing_ok=True)
        raise DeployError("下载到的文件不完整")

    _log(notify, "正在解压源码…")
    repo.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if not members:
                raise DeployError("源码压缩包是空的")
            root = members[0].name.split("/")[0]
            for member in members:
                name = member.name
                if not name.startswith(root + "/"):
                    continue
                member.name = name[len(root) + 1:]
                if not member.name:
                    continue
                try:
                    tar.extract(member, path=repo, filter="data")
                except TypeError:  # Python < 3.11.4 没有 filter 参数
                    tar.extract(member, path=repo)
    except (tarfile.TarError, OSError) as exc:
        raise DeployError(f"解压失败：{exc}") from exc
    finally:
        archive.unlink(missing_ok=True)

    # 只有完整解压成功才写标记，下次直接复用，避免用到解压到一半的源码
    try:
        (repo / ".source-complete").write_text("ok", encoding="utf-8")
    except OSError:
        pass


def create_source_archive(repo: Path | str, dest: Path | str | None = None) -> Path:
    """把源码目录打包成 tar.gz（排除 .git / node_modules 等），用于上传。

    打包时直接把目录内容放在压缩包根目录（没有顶层目录），
    这样服务器上解压不需要 --strip-components，busybox tar 也能用。
    gzip 头里的 mtime 固定为 0，同样的源码每次打出来的 sha256 一致，
    服务器上才能判断“已经传过同一份源码”。
    """
    repo_path = Path(repo)
    target = Path(dest) if dest else repo_path.parent / "open-xiaoai-src.tar.gz"
    excluded = {".git", "node_modules", "__pycache__", ".venv", "target", "dist", ".idea", ".vscode", ".source-complete"}

    def _filter(info: tarfile.TarInfo):
        if any(part in excluded for part in Path(info.name).parts):
            return None
        return info

    with open(target, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for child in sorted(repo_path.iterdir()):
                tar.add(str(child), arcname=child.name, filter=_filter)
    return target


def build_local_image(
    mode_key: str,
    base_dir: Path | str,
    mirror: str = "auto",
    custom_prefix: str = "",
    notify: Notify = None,
    build_proxy: str = "",
) -> str:
    """本机准备源码并构建镜像，返回本地镜像 tag。"""
    repo = fetch_source_bundle(base_dir, mirror, custom_prefix, notify)
    _prepare_source_tree(mode_key, repo)

    _log(notify, "开始构建镜像，首次构建可能需要几分钟（ARM / 小内存机器会更久）…", "warn")
    if build_proxy:
        _log(notify, _proxy_log_text(build_proxy))
    code, _ = _stream(["docker", *docker_build_args(mode_key, repo, build_proxy)], notify)
    if code != 0:
        raise DeployError(f"镜像构建失败。小内存机器建议先加 swap 再试：\n  {SWAP_COMMAND}")
    _log(notify, f"本地镜像构建完成：{source_image_tag(mode_key)}", "ok")
    return source_image_tag(mode_key)


# ---------------- 本机部署 ----------------


@dataclass
class DeployResult:
    mode_key: str
    container: str
    host: str
    port: int
    target: str  # local | remote

    @property
    def url(self) -> str:
        return connection_url(self.host, self.port)


def deploy_local(
    mode_key: str,
    values: dict[str, str],
    port: int = DEFAULT_PORT,
    base_dir: Path | str | None = None,
    method: str = "image",
    memory: str = "",
    mirror: str = "auto",
    custom_prefix: str = "",
    image_prefix: str = "",
    build_proxy: str = "",
    notify: Notify = None,
) -> DeployResult:
    spec = get_mode(mode_key)
    if not spec.deployable:
        raise DeployError(spec.note or "该接入方式暂不支持一键部署")

    errors = validate(mode_key, values)
    if errors:
        raise DeployError("；".join(errors))

    memory_error = validate_memory(memory)
    if memory_error:
        raise DeployError(memory_error)

    ready, detail = check_docker()
    if not ready:
        raise DeployError(f"{detail}。请先安装并启动 Docker Desktop，或在远程服务器上部署。")

    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent / "deploy"
    config_path = write_config(mode_key, values, base)
    _log(notify, f"配置文件已生成：{config_path}", "ok")

    env = detect_local_env(notify)
    if env:
        arch, memory_gb = env
        if method == "image" and arch.lower() not in ("x86_64", "amd64"):
            _log(notify, f"⚠️ 本机架构是 {arch}，官方镜像可能没有对应版本，建议改用「源码构建」。", "warn")
        if method == "source" and memory_gb < 2:
            _log(notify, f"⚠️ 本机内存只有 {memory_gb:.1f} GB，编译可能失败，建议先加 swap。", "warn")

    if method == "source":
        _status(notify, "正在准备源码并构建镜像…")
        image = build_local_image(mode_key, base, mirror, custom_prefix, notify, build_proxy)
    else:
        image = apply_image_prefix(spec.image, image_prefix)
        _status(notify, "正在拉取镜像…")
        if image != spec.image:
            _log(notify, f"使用镜像加速地址：{image}")
        code, output = _stream(["docker", "pull", image], notify)
        if code != 0:
            if "no matching manifest" in output or "no matching manifest for" in output:
                raise DeployError(
                    "这个镜像没有当前架构的版本（常见于 ARM 服务器）。"
                    "请在部署选项里勾选「源码构建」，在目标机器上重新编译。"
                )
            raise DeployError("镜像拉取失败，请检查网络或 Docker 代理设置。")

    _status(notify, "正在启动容器…")
    _stream(["docker", "rm", "-f", container_name(mode_key)], notify)
    run_args = docker_run_args(
        mode_key,
        config_path,
        port,
        memory=memory,
        image=image,
        source_build=(method == "source"),
    )
    if memory:
        _log(notify, f"容器内存上限：{memory}（swap 上限 {_memory_swap(memory)}）")
        if method == "source" and mode_key == "migpt":
            heap = _node_heap_mb(memory)
            if heap:
                _log(notify, f"Node 堆上限：{heap} MB（约为容器内存的一半）")
    code, output = _stream(["docker", *run_args], notify)
    if code != 0:
        if "port is already allocated" in output:
            raise DeployError(f"端口 {port} 已被占用，请换一个端口后重试。")
        raise DeployError(f"容器启动失败：{output.strip().splitlines()[-1] if output.strip() else '未知错误'}")

    _status(notify, "正在检查运行状态…")
    code, output = _stream(
        ["docker", "ps", "--filter", f"name={container_name(mode_key)}", "--format", "{{.Status}}"],
        notify,
    )
    if not output.strip():
        raise DeployError("容器没有运行起来，请查看日志排查。")

    host = get_local_ip()
    _log(notify, f"✅ 部署完成，客户端连接地址：{connection_url(host, port)}", "ok")
    _status(notify, "✅ 部署完成")
    return DeployResult(mode_key=mode_key, container=container_name(mode_key), host=host, port=port, target="local")


def stop_local(mode_key: str, notify: Notify = None) -> None:
    container = container_name(mode_key)
    _stream(["docker", "rm", "-f", container], notify)
    _log(notify, f"已停止并删除容器 {container}", "ok")


def logs_local(mode_key: str, notify: Notify = None, lines: int = 200) -> None:
    _stream(["docker", "logs", "--tail", str(lines), container_name(mode_key)], notify)


# ---------------- 远程部署 ----------------


@dataclass
class SshConfig:
    host: str
    port: int = 22
    user: str = "root"
    password: str = ""
    key_path: str = ""

    @property
    def home(self) -> str:
        return "/root" if self.user == "root" else f"/home/{self.user}"


def remote_config_dir(mode_key: str, ssh: SshConfig) -> str:
    """远程配置文件目录：root 用 /opt 下的独立子目录，普通用户退回 home。"""
    if ssh.user == "root":
        return f"/opt/open-xiaoai/{mode_key}"
    return f"{ssh.home}/open-xiaoai/{mode_key}"


def remote_config_path(mode_key: str, ssh: SshConfig) -> str:
    return f"{remote_config_dir(mode_key, ssh)}/{config_filename(mode_key)}"


def _require_paramiko():
    try:
        import paramiko  # noqa: PLC0415
    except ImportError as exc:
        raise DeployError(
            "远程部署需要 paramiko：请先在命令行执行 pip install paramiko，然后重新打开本工具。"
        ) from exc
    return paramiko


def _load_private_key(paramiko, path: str):
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key_file(path)
        except Exception:  # noqa: BLE001 - 换一种类型继续尝试
            continue
    raise DeployError(f"无法读取 SSH 私钥：{path}")


def _legacy_rsa_key(paramiko):
    """音箱上的旧 dropbear 只提供 ssh-rsa（SHA-1）主机密钥，新版 paramiko 默认禁用了它。"""
    from cryptography.hazmat.primitives import hashes  # paramiko 自带依赖

    class LegacyRSAKey(paramiko.RSAKey):
        HASHES = {**paramiko.RSAKey.HASHES, "ssh-rsa": hashes.SHA1}

    return LegacyRSAKey


def _ssh_connect(ssh: SshConfig):
    paramiko = _require_paramiko()
    transport = None
    sock = None
    try:
        sock = socket.create_connection((ssh.host, ssh.port), timeout=15)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = 15
        transport.auth_timeout = 15
        # 兼容小爱音箱的旧 dropbear：允许 ssh-rsa 主机密钥 + SHA-1 校验
        if "ssh-rsa" not in transport._preferred_keys:
            transport._preferred_keys = ("ssh-rsa", *transport._preferred_keys)
            transport._key_info = dict(transport._key_info)
            transport._key_info["ssh-rsa"] = _legacy_rsa_key(paramiko)
        transport.start_client(timeout=15)
        if ssh.key_path:
            transport.auth_publickey(ssh.user, _load_private_key(paramiko, ssh.key_path))
        elif ssh.password:
            transport.auth_password(ssh.user, ssh.password)
        else:
            raise DeployError("SSH 需要密码或私钥。")
        transport.set_keepalive(30)
    except DeployError:
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()
        raise
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()
        detail = str(exc).strip() or exc.__class__.__name__
        lowered = detail.lower()
        if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in lowered:
            reason = (
                f"连接 {ssh.host}:{ssh.port} 超时。请确认 IP / 端口正确、"
                "电脑和音箱在同一网段、音箱已开机。"
            )
        elif "authentication" in lowered or "auth fail" in lowered or "bad password" in lowered:
            reason = "用户名或密码错误（音箱默认 root / open-xiaoai）。"
        elif "connection refused" in lowered:
            reason = f"{ssh.host}:{ssh.port} 拒绝连接：确认 SSH 已开启、端口正确。"
        elif "no route to host" in lowered:
            reason = f"无法路由到 {ssh.host}：检查电脑和音箱的网络 / 网段。"
        elif "incompatible" in lowered or "host key" in lowered:
            reason = "主机密钥协商失败：服务器只支持旧版 SSH 算法。"
        elif "unable to connect" in lowered:
            reason = f"无法连接 {ssh.host}:{ssh.port}：检查 IP、端口和网络。"
        else:
            reason = detail
        raise DeployError(f"SSH 连接失败：{reason}") from exc
    client = paramiko.SSHClient()
    client._transport = transport
    return client


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _ssh_failure_text(exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    lowered = detail.lower()
    if isinstance(exc, EOFError) or "eof" in lowered or "socket is closed" in lowered:
        return (
            f"SSH 连接中断：{detail}\n"
            "服务器主动断开了连接，常见原因是内存不足（sshd 被系统杀掉）或网络抖动。"
            f"可以先在服务器上添加 2G swap 再重试：\n  {SWAP_COMMAND}"
        )
    return f"SSH 操作失败：{detail}"


# 网络类报错（Docker Hub 被墙 / DNS 失败 / 超时 / apt 拉不到包），不要误报成内存不足
_NETWORK_ERROR_MARKERS = (
    "context deadline exceeded",
    "i/o timeout",
    "tls handshake timeout",
    "no such host",
    "network is unreachable",
    "temporary failure in name resolution",
    "temporary failure resolving",
    "could not resolve",
    "failed to resolve",
    "failed to fetch",
    "unable to connect",
    "connection failed",
    "connection refused",
    "connection reset",
    "proxyconnect",
    "dial tcp",
)

DOCKER_MIRROR_HINT = (
    "给服务器配置 Docker 镜像加速（只解决拉镜像）：\n"
    "  sudo mkdir -p /etc/docker\n"
    "  echo '{\"registry-mirrors\": [\"https://docker.1ms.run\", \"https://docker.m.daocloud.io\"]}'"
    " | sudo tee /etc/docker/daemon.json\n"
    "  sudo systemctl restart docker\n"
)

BUILD_PROXY_HINT = (
    "如果服务器有能上外网的代理，在控制台「高级选项 → 构建代理」填上代理地址再部署，"
    "例如 http://172.17.0.1:7890（宿主机在 Docker 网桥里的地址）—— 构建里的拉镜像、apt、npm、rustup 都会走这个代理。"
)

APT_INDEX_HINT = (
    "apt 软件源索引里没有需要的包：通常是 apt-get update 没下载全，或者代理只对宿主机生效、构建容器里访问不到。\n"
    "1. 看上面 apt-get update 的输出：出现 Failed to fetch / Some index files failed to download 就是源没拉下来；\n"
    "2. 容器里的 127.0.0.1 是容器自己，连不到宿主机上的代理；apt 也不支持 socks5://，请填 http:// 地址。"
    "请填宿主机在 Docker 网桥里的地址（一般是 http://172.17.0.1:7890），并让代理监听 0.0.0.0（Clash 的「允许局域网」）；"
    "填 127.0.0.1 时控制台会自动换成 host.docker.internal；\n"
    "3. 或者把 Dockerfile 里的 deb.debian.org 换成国内源（mirrors.tuna.tsinghua.edu.cn、mirrors.aliyun.com）。"
)


def _container_proxy(proxy: str) -> str:
    """容器里的 127.0.0.1 是容器自己，构建代理要指向宿主机。"""
    return re.sub(r"(?i)//(?:127\.0\.0\.1|localhost)(?=[:/]|$)", "//host.docker.internal", proxy)


def _is_network_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _NETWORK_ERROR_MARKERS)


def _tail_lines(text: str, limit: int = 5) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _build_error_message(output: str, code: int) -> str:
    """把 docker build / pull 的失败翻译成可操作的提示。"""
    detail = _tail_lines(output) or f"退出码 {code}"
    lowered = (output or "").lower()
    if "no space left on device" in lowered:
        return (
            f"服务器磁盘空间不足：\n{detail}\n"
            "清理不需要的镜像和构建缓存后重试：\n"
            "  docker system df\n  docker image prune -f\n  docker builder prune -f"
        )
    apt_markers = (
        "is not available, but is referred to by another package",
        "has no installation candidate",
        "unable to locate package",
    )
    if any(marker in lowered for marker in apt_markers):
        return f"服务器安装构建依赖失败：\n{detail}\n{APT_INDEX_HINT}"
    if _is_network_error(output):
        return (
            f"服务器在构建时联网失败（拉镜像 / apt / npm 等），不是内存不足：\n{detail}\n"
            f"{DOCKER_MIRROR_HINT}{BUILD_PROXY_HINT}"
        )
    if code == 137 or "killed" in lowered or "out of memory" in lowered or "cannot allocate memory" in lowered:
        return f"服务器内存不足，构建进程被系统杀掉。建议先加 swap 再试：\n  {SWAP_COMMAND}"
    return (
        f"服务器上镜像构建失败（退出码 {code}）：\n{detail}\n"
        f"如果是内存不足，可以先加 swap 再试：\n  {SWAP_COMMAND}"
    )


def _ssh_run(client, command: str, notify: Notify = None, quiet: bool = False) -> tuple[int, str]:
    if not quiet:
        _log(notify, f"[远程] $ {command}")
    # stderr 要整条命令一起合并：只写在末尾的 2>&1 只对 && 串联的最后一条生效，
    # 前面 tar / rm 之类命令报错时就看不到原因，只能显示“未知错误”。
    try:
        _, stdout, _ = client.exec_command(f"exec 2>&1; {command}", get_pty=False)
        lines: list[str] = []
        for line in stdout:
            line = line.rstrip()
            lines.append(line)
            if line and not quiet:
                _log(notify, line)
        code = stdout.channel.recv_exit_status()
    except DeployError:
        raise
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        raise DeployError(_ssh_failure_text(exc)) from exc
    if code < 0:
        detail = "\n".join(line for line in lines if line.strip()) or "没有收到任何输出"
        raise DeployError(
            f"SSH 会话中断：服务器没有返回命令结果（{detail}）。"
            "可能是内存不足导致 sshd 断开了连接，建议先加 swap 后重试。"
        )
    return code, "\n".join(lines)


_NO_SFTP_ATTR = "_open_xiaoai_no_sftp"
UPLOAD_CHUNK = 256 * 1024


def _open_sftp(client, notify: Notify = None):
    """打开 SFTP 通道。服务器没有 sftp 子系统时返回 None，后续改用 SSH 通道传输。"""
    if getattr(client, _NO_SFTP_ATTR, False):
        return None
    try:
        return client.open_sftp()
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        setattr(client, _NO_SFTP_ATTR, True)
        _log(notify, f"服务器没有可用的 SFTP（{exc}），改用 SSH 通道传输文件。", "warn")
        return None


def _read_chunks(path: Path):
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(UPLOAD_CHUNK)
            if not chunk:
                return
            yield chunk


def _upload_stream(client, remote_path: str, chunks, total: int, notify: Notify = None, callback=None) -> None:
    """通过普通 SSH 通道（cat > 文件）上传，不依赖服务器的 sftp 子系统。"""
    command = f"cat > {shlex.quote(remote_path)}"
    _log(notify, f"[远程] $ {command}")
    try:
        stdin, stdout, stderr = client.exec_command(command, get_pty=False)
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        raise DeployError(_ssh_failure_text(exc)) from exc

    sent = 0
    write_error: Optional[BaseException] = None
    try:
        for chunk in chunks:
            stdin.write(chunk)
            sent += len(chunk)
            if callback and total:
                callback(sent, total)
        stdin.flush()
    except Exception as exc:  # noqa: BLE001 - 远端提前退出时这里会拿到断开的管道
        write_error = exc
    try:
        stdin.channel.shutdown_write()
    except Exception:  # noqa: BLE001 - 连接已断开时忽略
        pass
    try:
        error = _as_text(stderr.read()).strip()
        code = stdout.channel.recv_exit_status()
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        raise DeployError(_ssh_failure_text(exc)) from exc
    if code != 0 or write_error is not None:
        reason = error or str(write_error or f"退出码 {code}")
        raise DeployError(f"写入远程文件失败：{remote_path}（{reason}）")


def _upload_text(client, remote_path: str, text: str, notify: Notify = None) -> None:
    sftp = _open_sftp(client, notify)
    if sftp is not None:
        try:
            with sftp.file(remote_path, "w") as handle:
                handle.write(text)
            return
        except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
            raise DeployError(f"写入远程文件失败：{remote_path}（{exc}）") from exc
        finally:
            sftp.close()
    data = text.encode("utf-8")
    _upload_stream(client, remote_path, [data], len(data), notify)


def _upload_file(client, local_path, remote_path: str, notify: Notify = None, callback=None) -> None:
    local = Path(local_path)
    sftp = _open_sftp(client, notify)
    if sftp is not None:
        try:
            sftp.put(str(local), remote_path, callback=callback)
            return
        except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
            raise DeployError(f"上传文件到服务器失败：{remote_path}（{exc}）") from exc
        finally:
            sftp.close()
    total = local.stat().st_size
    _upload_stream(client, remote_path, _read_chunks(local), total, notify, callback)


def _download_file(client, remote_path: str, local_path, notify: Notify = None) -> None:
    local = Path(local_path)
    sftp = _open_sftp(client, notify)
    if sftp is not None:
        try:
            sftp.get(remote_path, str(local))
            return
        except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
            raise DeployError(f"从服务器下载文件失败：{remote_path}（{exc}）") from exc
        finally:
            sftp.close()

    command = f"cat {shlex.quote(remote_path)}"
    _log(notify, f"[远程] $ {command}")
    try:
        _, stdout, stderr = client.exec_command(command, get_pty=False)
        with open(local, "wb") as handle:
            while True:
                chunk = stdout.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
        error = _as_text(stderr.read()).strip()
        code = stdout.channel.recv_exit_status()
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        local.unlink(missing_ok=True)
        raise DeployError(_ssh_failure_text(exc)) from exc
    if code != 0:
        local.unlink(missing_ok=True)
        raise DeployError(f"从服务器下载文件失败：{remote_path}（{error or f'退出码 {code}'}）")


def _meminfo_gb(text: str, key: str) -> float | None:
    match = re.search(rf"^{key}:\s+(\d+)\s*kB", text, re.MULTILINE)
    if not match:
        return None
    return round(int(match.group(1)) / 1024 / 1024, 1)


def _swap_setup_command() -> str:
    """幂等地创建 2G swapfile：已启用就跳过，磁盘不够就跳过，成功会写进 /etc/fstab。"""
    return (
        "if grep -q '^/swapfile ' /proc/swaps; then echo 'swapfile 已启用'; exit 0; fi\n"
        "free_mb=$(df -Pm / | awk 'NR==2 {print $4}')\n"
        "if [ -z \"$free_mb\" ] || [ \"$free_mb\" -lt 3072 ]; then echo \"磁盘剩余 ${free_mb}MB，跳过创建 swap\"; exit 0; fi\n"
        "fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 2>/dev/null\n"
        "chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile "
        "|| { echo '创建 swap 失败（需要 root 权限）'; exit 1; }\n"
        "grep -qs '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab\n"
        "echo 'swap 已启用'"
    )


def _remote_swap_gb(client) -> float:
    code, output = _ssh_run(
        client,
        "awk '/^SwapTotal:/ {printf \"%.1f\", $2/1024/1024}' /proc/meminfo",
        quiet=True,
    )
    if code != 0:
        return 0.0
    try:
        return float(output.strip() or 0)
    except ValueError:
        return 0.0


def ensure_remote_swap(client, swap_gb: float = 0.0, notify: Notify = None) -> bool:
    """服务器没启用 swap 时创建一个 2G swapfile，返回是否已启用。

    小内存服务器编译镜像容易触发 OOM，加 swap 是最省事的办法；只需要做一次。
    """
    if swap_gb >= 0.1:
        return True
    _log(notify, "服务器没有启用 swap，正在创建 2G swap（只需做一次，编译更稳）…")
    code, _ = _ssh_run(client, _swap_setup_command(), notify)
    if code != 0:
        _log(
            notify,
            "创建 swap 失败，继续部署（编译阶段可能因内存不足失败）。也可以手动执行：\n  " + SWAP_COMMAND,
            "warn",
        )
        return False
    swap_after = _remote_swap_gb(client)
    if swap_after >= 0.1:
        _log(notify, f"✅ swap 已启用：{swap_after:.1f} GB", "ok")
        return True
    _log(notify, "没有检测到新的 swap，继续部署（编译阶段可能因内存不足失败）。", "warn")
    return False


def detect_remote_env(client, notify: Notify = None) -> tuple[str, float, float] | None:
    """返回服务器架构、内存（GB）、swap（GB）。"""
    code, output = _ssh_run(
        client,
        "uname -m; grep -E '^(MemTotal|SwapTotal):' /proc/meminfo",
        notify,
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    memory_gb = _meminfo_gb(output, "MemTotal")
    if memory_gb is None:
        return None
    swap_gb = _meminfo_gb(output, "SwapTotal") or 0.0
    _log(notify, f"服务器：架构 {lines[0]}，内存约 {memory_gb:.1f} GB，swap {swap_gb:.1f} GB")
    return lines[0], memory_gb, swap_gb


def remote_extract_command(remote_tar: str, remote_src: str) -> str:
    # 源码包是「平铺」的（没有顶层目录），不需要 --strip-components；
    # 部分精简系统的 busybox tar 不支持这个参数，会直接报错。
    # 源码包保留在服务器上（不删），下次内容没变就可以直接跳过上传。
    return (
        f"rm -rf {remote_src} && mkdir -p {remote_src} && "
        f"tar -xzf {remote_tar} -C {remote_src}"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_marker_matches(client, path: str, value: str) -> bool:
    """服务器上的标记文件内容等于 value 时返回 True（用于跳过已经做过的步骤）。"""
    code, output = _ssh_run(client, f"cat {shlex.quote(path)}", quiet=True)
    return code == 0 and output.strip() == value


def _remote_file_matches(client, path: str, digest: str) -> bool:
    """服务器上的文件 sha256 等于 digest 时返回 True。"""
    quoted = shlex.quote(path)
    code, output = _ssh_run(client, f"sha256sum {quoted}", quiet=True)
    if code != 0:
        return False
    first = output.split()[0] if output.split() else ""
    return first == digest


def _write_remote_marker(client, path: str, value: str) -> None:
    _ssh_run(client, f"printf '%s' {shlex.quote(value)} > {shlex.quote(path)}", quiet=True)


def _remote_image_exists(client, tag: str) -> bool:
    code, _ = _ssh_run(client, f"docker image inspect -f '{{{{.Id}}}}' {tag}", quiet=True)
    return code == 0


def build_remote_image(
    client,
    mode_key: str,
    ssh: SshConfig,
    local_repo: Path | str,
    notify: Notify = None,
    build_proxy: str = "",
) -> str:
    """把本机源码打包上传到服务器，再在服务器上构建；服务器不需要 git，也不用访问 GitHub。

    每一步都先检查服务器上是否已经做过，内容相同就跳过：
    - 源码包已存在且 sha256 一致 → 跳过上传；
    - 源码已解压且标记文件一致 → 跳过解压；
    - 同一份源码已经构建过镜像 → 跳过构建。
    """
    _prepare_source_tree(mode_key, local_repo)
    archive = create_source_archive(local_repo)
    try:
        digest = _sha256_file(archive)
        size_mb = archive.stat().st_size / (1024 ** 2)
        remote_root = f"{ssh.home}/open-xiaoai"
        remote_tar = f"{remote_root}/src.tar.gz"
        remote_src = f"{remote_root}/src"
        src_marker = f"{remote_src}/.open-xiaoai-src"
        built_marker = f"{remote_src}/.open-xiaoai-built"

        _ssh_run(client, f"mkdir -p {remote_root}", notify)

        if _remote_file_matches(client, remote_tar, digest):
            _log(notify, "服务器上已有同一份源码包，跳过上传", "ok")
        else:
            _log(notify, f"正在上传源码包（{size_mb:.1f} MB）…")
            last_percent = -1

            def _progress(transferred: int, total: int) -> None:
                nonlocal last_percent
                if not total:
                    return
                percent = int(transferred * 100 / total)
                if percent >= last_percent + 10:
                    last_percent = percent
                    _log(notify, f"上传源码… {percent}%")
                    _status(notify, f"正在上传源码… {percent}%")

            _upload_file(client, archive, remote_tar, notify, _progress)
    finally:
        archive.unlink(missing_ok=True)

    if _remote_marker_matches(client, src_marker, digest):
        _log(notify, "服务器上已是同一份源码，跳过解压", "ok")
    else:
        _status(notify, "正在服务器上解压源码…")
        code, output = _ssh_run(client, remote_extract_command(remote_tar, remote_src), notify)
        if code != 0:
            lines = [line for line in output.strip().splitlines() if line.strip()]
            detail = "\n".join(lines[:3]) if lines else f"退出码 {code}"
            raise DeployError(f"服务器解压源码失败：{detail}")
        _write_remote_marker(client, src_marker, digest)

    tag = source_image_tag(mode_key)
    dockerfile_name = "Dockerfile"
    if mode_key == "migpt" and (Path(local_repo) / "examples" / "migpt" / "Dockerfile.lean").is_file():
        dockerfile_name = "Dockerfile.lean"

    if _remote_marker_matches(client, built_marker, digest) and _remote_image_exists(client, tag):
        _log(notify, f"服务器上已有同一份源码构建的镜像，跳过构建：{tag}", "ok")
        _status(notify, "已跳过镜像构建")
        return tag

    _log(notify, "开始在服务器上构建镜像，首次可能需要几分钟（ARM / 小内存机器会更久）…", "warn")
    if build_proxy:
        _log(notify, _proxy_log_text(build_proxy))
    build = [
        "docker",
        "build",
        "-f",
        f"{remote_src}/examples/{mode_key}/{dockerfile_name}",
        "-t",
        tag,
        *_proxy_build_args(build_proxy),
        remote_src,
    ]
    code, output = _ssh_run(client, shlex.join(build), notify)
    if code != 0:
        raise DeployError(_build_error_message(output, code))
    _write_remote_marker(client, built_marker, digest)
    _log(notify, f"服务器镜像构建完成：{tag}", "ok")
    return tag


def deploy_remote(
    mode_key: str,
    values: dict[str, str],
    ssh: SshConfig,
    port: int = DEFAULT_PORT,
    method: str = "image",
    memory: str = "",
    mirror: str = "auto",
    custom_prefix: str = "",
    image_prefix: str = "",
    build_proxy: str = "",
    base_dir: Path | str | None = None,
    notify: Notify = None,
) -> DeployResult:
    spec = get_mode(mode_key)
    if not spec.deployable:
        raise DeployError(spec.note or "该接入方式暂不支持一键部署")

    errors = validate(mode_key, values)
    if errors:
        raise DeployError("；".join(errors))

    memory_error = validate_memory(memory)
    if memory_error:
        raise DeployError(memory_error)

    _status(notify, "正在连接服务器…")
    client = _ssh_connect(ssh)
    try:
        code, _ = _ssh_run(client, "docker --version", notify)
        if code != 0:
            raise DeployError(
                "服务器上没有可用的 Docker。请先安装 Docker 后再试，"
                "例如：curl -fsSL https://get.docker.com | sudo sh"
            )

        env = detect_remote_env(client, notify)
        if env:
            arch, memory_gb, swap_gb = env
            if method == "image" and arch.lower() not in ("x86_64", "amd64"):
                _log(
                    notify,
                    f"⚠️ 服务器架构是 {arch}，官方镜像可能没有对应版本，建议勾选「源码构建」。",
                    "warn",
                )
            if method == "source" and memory_gb < 2:
                if swap_gb >= 0.1:
                    _log(
                        notify,
                        f"⚠️ 服务器内存 {memory_gb:.1f} GB、swap {swap_gb:.1f} GB，编译会比较慢。",
                        "warn",
                    )
                else:
                    ensure_remote_swap(client, swap_gb, notify)

        config_text = render_config(mode_key, values)
        remote_path = remote_config_path(mode_key, ssh)

        _status(notify, "正在上传配置…")
        _ssh_run(client, f"mkdir -p {shlex.quote(remote_config_dir(mode_key, ssh))}", notify)
        _upload_text(client, remote_path, config_text, notify)
        _log(notify, f"配置已上传：{remote_path}", "ok")

        if method == "source":
            _status(notify, "正在本机准备源码…")
            base = Path(base_dir) if base_dir else Path(__file__).resolve().parent / "deploy"
            repo = fetch_source_bundle(base, mirror, custom_prefix, notify)
            _status(notify, "正在上传源码并构建…")
            image = build_remote_image(client, mode_key, ssh, repo, notify, build_proxy)
        else:
            image = apply_image_prefix(spec.image, image_prefix)
            _status(notify, "正在拉取镜像…")
            if image != spec.image:
                _log(notify, f"使用镜像加速地址：{image}")
            code, output = _ssh_run(client, f"docker pull {image}", notify)
            if code != 0:
                if "no matching manifest" in output:
                    raise DeployError(
                        "这个镜像没有服务器架构的版本（常见于 ARM）。"
                        "请在部署选项里勾选「源码构建」，在服务器上重新编译。"
                    )
                if _is_network_error(output):
                    raise DeployError(
                        f"镜像拉取失败（网络问题，不是内存不足）：\n{_tail_lines(output)}\n{DOCKER_MIRROR_HINT}"
                        "镜像站会变化，一个连不上就换一个；也可以给 Docker 服务配置 HTTPS_PROXY。"
                    )
                raise DeployError("镜像拉取失败，请检查服务器的网络或 Docker 配置。")

        _status(notify, "正在启动容器…")
        _ssh_run(client, f"docker rm -f {container_name(mode_key)}", notify)
        run_parts = [
            f"docker run -d --name {container_name(mode_key)} --restart unless-stopped",
            f"-p {port}:{DEFAULT_PORT}",
        ]
        if memory:
            run_parts.append(f"--memory {memory} --memory-swap {_memory_swap(memory)}")
            _log(notify, f"容器内存上限：{memory}（swap 上限 {_memory_swap(memory)}）")
        if method == "source" and mode_key == "migpt":
            heap = _node_heap_mb(memory)
            if heap:
                run_parts.append(f'-e "NODE_OPTIONS=--max-old-space-size={heap} --max-semi-space-size=8"')
                _log(notify, f"Node 堆上限：{heap} MB（约为容器内存的一半）")
        run_parts.append(f"-v {remote_path}:/app/{spec.config_name} {image}")
        run = " ".join(run_parts)
        code, output = _ssh_run(client, run, notify)
        if code != 0:
            if "port is already allocated" in output:
                raise DeployError(f"端口 {port} 已被占用，请换一个端口后重试。")
            raise DeployError(f"容器启动失败：{output.strip().splitlines()[-1] if output.strip() else '未知错误'}")

        code, status = _ssh_run(
            client,
            f"docker ps --filter name={container_name(mode_key)} --format '{{{{.Status}}}}'",
            notify,
        )
        if not status.strip():
            raise DeployError("容器没有运行起来，请查看远程日志排查。")

        _log(notify, f"✅ 部署完成，客户端连接地址：{connection_url(ssh.host, port)}", "ok")
        _status(notify, "✅ 部署完成")
        return DeployResult(mode_key=mode_key, container=container_name(mode_key), host=ssh.host, port=port, target="remote")
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def stop_remote(mode_key: str, ssh: SshConfig, notify: Notify = None) -> None:
    client = _ssh_connect(ssh)
    try:
        _ssh_run(client, f"docker rm -f {container_name(mode_key)}", notify)
        _log(notify, "已停止远程容器。", "ok")
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def logs_remote(mode_key: str, ssh: SshConfig, notify: Notify = None, lines: int = 200) -> None:
    client = _ssh_connect(ssh)
    try:
        _ssh_run(client, f"docker logs --tail {lines} {container_name(mode_key)}", notify)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------- 模型配置（容器挂载的配置文件） ----------------


def _config_destination(mode_key: str) -> str:
    return f"/app/{config_filename(mode_key)}"


def _mount_source(text: str, mode_key: str) -> str:
    """从 docker inspect -f '{{json .Mounts}}' 的输出里找配置文件在宿主机上的路径。"""
    destination = _config_destination(mode_key)
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    if not lines:
        return ""
    try:
        mounts = json.loads(lines[-1])
    except ValueError:
        return ""
    if not isinstance(mounts, list):
        return ""
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == destination:
            return str(mount.get("Source") or "")
    return ""


def _local_config_source(mode_key: str) -> tuple[str, bool]:
    """返回 (容器配置文件在宿主机上的路径, 容器是否存在)。"""
    container = container_name(mode_key)
    code, output = _stream(["docker", "inspect", "-f", "{{json .Mounts}}", container])
    if code != 0:
        return "", False
    return _mount_source(output, mode_key), True


def _remote_config_source(client, mode_key: str) -> tuple[str, bool]:
    container = container_name(mode_key)
    code, output = _ssh_run(
        client, f"docker inspect -f '{{{{json .Mounts}}}}' {container}", quiet=True
    )
    if code != 0:
        return "", False
    return _mount_source(output, mode_key), True


def save_model_config(
    mode_key: str,
    values: dict[str, str],
    target: str = "local",
    ssh: SshConfig | None = None,
    restart: bool = True,
    notify: Notify = None,
) -> dict:
    """实时修改模型配置：写到容器挂载的磁盘文件，再（可选）重启容器生效。"""
    errors = validate(mode_key, values)
    if errors:
        raise DeployError("；".join(errors))
    text = render_config(mode_key, values)
    container = container_name(mode_key)

    if target == "remote":
        if ssh is None:
            raise DeployError("远程服务器需要 SSH 配置。")
        client = _ssh_connect(ssh)
        try:
            source, exists = _remote_config_source(client, mode_key)
            if not exists:
                raise DeployError(f"服务器上没有找到容器 {container}，请先在部署页部署。")
            if not source:
                source = remote_config_path(mode_key, ssh)
            _ssh_run(client, f"mkdir -p {shlex.quote(posixpath.dirname(source))}", quiet=True)
            _upload_text(client, source, text, notify)
            _log(notify, f"模型配置已写入：{source}", "ok")
            if restart:
                code, output = _ssh_run(client, f"docker restart {container}", notify)
                if code != 0:
                    raise DeployError(f"重启容器失败：{_tail_lines(output) or f'退出码 {code}'}")
                _log(notify, f"容器已重启：{container}", "ok")
            return {"path": source, "container": container, "restarted": restart}
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    source, exists = _local_config_source(mode_key)
    if not exists:
        raise DeployError(f"本机没有找到容器 {container}，请先在部署页部署。")
    if not source:
        source = str(write_config(mode_key, values, Path(__file__).resolve().parent / "deploy"))
    else:
        Path(source).write_text(text, encoding="utf-8")
    _log(notify, f"模型配置已写入：{source}", "ok")
    if restart:
        code, output = _stream(["docker", "restart", container], notify)
        if code != 0:
            raise DeployError(f"重启容器失败：{_tail_lines(output) or f'退出码 {code}'}")
        _log(notify, f"容器已重启：{container}", "ok")
    return {"path": source, "container": container, "restarted": restart}


def restart_container(
    mode_key: str,
    target: str = "local",
    ssh: SshConfig | None = None,
    notify: Notify = None,
) -> dict:
    container = container_name(mode_key)
    if target == "remote":
        if ssh is None:
            raise DeployError("远程服务器需要 SSH 配置。")
        client = _ssh_connect(ssh)
        try:
            code, output = _ssh_run(client, f"docker restart {container}", notify)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    else:
        code, output = _stream(["docker", "restart", container], notify)
    if code != 0:
        raise DeployError(f"重启容器失败：{_tail_lines(output) or f'退出码 {code}'}")
    _log(notify, f"容器已重启：{container}", "ok")
    return {"container": container, "restarted": True}


def client_commands(url: str) -> str:
    """给音箱端 Client 用的命令（见 packages/client-rust/README.md）。"""
    return (
        f"mkdir -p /data/open-xiaoai\n"
        f"echo '{url}' > /data/open-xiaoai/server.txt\n"
        f"curl -sSfL https://gitee.com/idootop/artifacts/releases/download/open-xiaoai-client/init.sh | sh"
    )
