"""Open-XiaoAI 服务端部署核心逻辑。

根据仓库 examples/ 下的文档，把「大模型接入方式」抽象成可部署的模式：
- 小智 AI：官方 Docker 镜像 + config.py
- MiGPT：官方 Docker 镜像 + config.ts

支持部署到本机 Docker 或远程服务器（SSH）。
"""

from __future__ import annotations

import json
import os
import re
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
            default="https://api.openai.com/v1",
            required=True,
            options=(
                "https://api.openai.com/v1",
                "https://api.deepseek.com",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "https://api.moonshot.cn/v1",
                "https://open.bigmodel.cn/api/paas/v4",
                "https://api.siliconflow.cn/v1",
                "https://ark.cn-beijing.volces.com/api/v3",
            ),
        ),
        Field(
            key="model",
            label="模型名称",
            default="gpt-4.1-mini",
            required=True,
            options=(
                "gpt-4.1-mini",
                "gpt-4.1",
                "gpt-4o-mini",
                "gpt-4o",
                "deepseek-flash",
                "deepseek-v4-pro",
                "qwen-plus",
                "qwen-max",
                "qwen-turbo",
                "moonshot-v1-8k",
                "moonshot-v1-32k",
                "glm-4-plus",
                "glm-4-flash",
            ),
        ),
        Field(
            key="system_prompt",
            label="系统提示词",
            default="你是一个智能助手，请根据用户的问题给出回答。",
            multiline=True,
        ),
    ),
    note="支持 DeepSeek、通义、Moonshot 等任何兼容 OpenAI 接口的服务，改接口地址和模型名即可。",
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
    target.write_bytes(MIGPT_LEAN_DOCKERFILE.read_bytes())


def docker_build_args(mode_key: str, repo_dir: Path | str) -> list[str]:
    repo = Path(repo_dir)
    dockerfile = repo / "examples" / mode_key / "Dockerfile"
    if mode_key == "migpt":
        lean = repo / "examples" / "migpt" / "Dockerfile.lean"
        if lean.is_file():
            dockerfile = lean
    return ["build", "-f", str(dockerfile), "-t", source_image_tag(mode_key), str(repo)]


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


def fetch_source_bundle(
    base_dir: Path | str,
    mirror: str = "auto",
    custom_prefix: str = "",
    notify: Notify = None,
) -> Path:
    """在本机准备源码：有缓存直接用，没有就按加速源下载压缩包。"""
    repo = source_repo_dir(base_dir)

    if (repo / "examples").is_dir():
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


def create_source_archive(repo: Path | str, dest: Path | str | None = None) -> Path:
    """把源码目录打包成 tar.gz（排除 .git / node_modules 等），用于上传。"""
    repo_path = Path(repo)
    target = Path(dest) if dest else repo_path.parent / "open-xiaoai-src.tar.gz"
    excluded = {".git", "node_modules", "__pycache__", ".venv", "target", "dist", ".idea", ".vscode"}

    def _filter(info: tarfile.TarInfo):
        if any(part in excluded for part in Path(info.name).parts):
            return None
        return info

    with tarfile.open(target, "w:gz") as tar:
        tar.add(str(repo_path), arcname=repo_path.name, filter=_filter)
    return target


def build_local_image(
    mode_key: str,
    base_dir: Path | str,
    mirror: str = "auto",
    custom_prefix: str = "",
    notify: Notify = None,
) -> str:
    """本机准备源码并构建镜像，返回本地镜像 tag。"""
    repo = fetch_source_bundle(base_dir, mirror, custom_prefix, notify)
    _prepare_source_tree(mode_key, repo)

    _log(notify, "开始构建镜像，首次构建可能需要几分钟（ARM / 小内存机器会更久）…", "warn")
    code, _ = _stream(["docker", *docker_build_args(mode_key, repo)], notify)
    if code != 0:
        raise DeployError(
            "镜像构建失败。小内存机器建议先加 swap 再试：\n"
            "  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && "
            "sudo mkswap /swapfile && sudo swapon /swapfile"
        )
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
        image = build_local_image(mode_key, base, mirror, custom_prefix, notify)
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


def remote_config_path(mode_key: str, ssh: SshConfig) -> str:
    return f"{ssh.home}/open-xiaoai/{mode_key}/{config_filename(mode_key)}"


def _require_paramiko():
    try:
        import paramiko  # noqa: PLC0415
    except ImportError as exc:
        raise DeployError(
            "远程部署需要 paramiko：请先在命令行执行 pip install paramiko，然后重新打开本工具。"
        ) from exc
    return paramiko


def _ssh_connect(ssh: SshConfig):
    paramiko = _require_paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ssh.host,
            port=ssh.port,
            username=ssh.user,
            password=ssh.password or None,
            key_filename=ssh.key_path or None,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 - paramiko 异常类型很多
        raise DeployError(f"SSH 连接失败：{exc}") from exc
    return client


def _ssh_run(client, command: str, notify: Notify = None) -> tuple[int, str]:
    command = f"{command} 2>&1"  # Docker 的进度和错误都在 stderr，合并后统一展示
    _log(notify, f"[远程] $ {command}")
    _, stdout, _ = client.exec_command(command, get_pty=False)
    lines: list[str] = []
    for line in stdout:
        line = line.rstrip()
        lines.append(line)
        if line:
            _log(notify, line)
    code = stdout.channel.recv_exit_status()
    return code, "\n".join(lines)


def detect_remote_env(client, notify: Notify = None) -> tuple[str, float] | None:
    """返回服务器架构和内存（GB）。"""
    code, output = _ssh_run(
        client,
        "uname -m; awk '/MemTotal/ {printf \"%.1f\", $2/1024/1024}' /proc/meminfo",
        notify,
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    try:
        memory_gb = float(lines[1])
    except ValueError:
        return None
    _log(notify, f"服务器：架构 {lines[0]}，内存约 {memory_gb:.1f} GB")
    return lines[0], memory_gb


def remote_extract_command(remote_tar: str, remote_src: str) -> str:
    return (
        f"rm -rf {remote_src} && mkdir -p {remote_src} && "
        f"tar -xzf {remote_tar} -C {remote_src} --strip-components=1 && rm -f {remote_tar}"
    )


def build_remote_image(
    client,
    mode_key: str,
    ssh: SshConfig,
    local_repo: Path | str,
    notify: Notify = None,
) -> str:
    """把本机源码打包上传到服务器，再在服务器上构建；服务器不需要 git，也不用访问 GitHub。"""
    _prepare_source_tree(mode_key, local_repo)
    archive = create_source_archive(local_repo)
    size_mb = archive.stat().st_size / (1024 ** 2)
    remote_root = f"{ssh.home}/open-xiaoai"
    remote_tar = f"{remote_root}/src.tar.gz"
    remote_src = f"{remote_root}/src"

    _log(notify, f"正在上传源码包（{size_mb:.1f} MB）…")
    _ssh_run(client, f"mkdir -p {remote_root}", notify)

    sftp = client.open_sftp()
    try:
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

        sftp.put(str(archive), remote_tar, callback=_progress)
    finally:
        sftp.close()
        archive.unlink(missing_ok=True)

    _status(notify, "正在服务器上解压源码…")
    code, output = _ssh_run(client, remote_extract_command(remote_tar, remote_src), notify)
    if code != 0:
        detail = output.strip().splitlines()[-1] if output.strip() else "未知错误"
        raise DeployError(f"服务器解压源码失败：{detail}")

    tag = source_image_tag(mode_key)
    dockerfile_name = "Dockerfile"
    if mode_key == "migpt" and (Path(local_repo) / "examples" / "migpt" / "Dockerfile.lean").is_file():
        dockerfile_name = "Dockerfile.lean"
    _log(notify, "开始在服务器上构建镜像，首次可能需要几分钟（ARM / 小内存机器会更久）…", "warn")
    code, _ = _ssh_run(
        client,
        f"docker build -f {remote_src}/examples/{mode_key}/{dockerfile_name} -t {tag} {remote_src}",
        notify,
    )
    if code != 0:
        raise DeployError(
            "服务器上镜像构建失败。内存不足时建议先加 swap 再试：\n"
            "sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && "
            "sudo mkswap /swapfile && sudo swapon /swapfile"
        )

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
            arch, memory_gb = env
            if method == "image" and arch.lower() not in ("x86_64", "amd64"):
                _log(
                    notify,
                    f"⚠️ 服务器架构是 {arch}，官方镜像可能没有对应版本，建议勾选「源码构建」。",
                    "warn",
                )
            if method == "source" and memory_gb < 2:
                _log(
                    notify,
                    f"⚠️ 服务器内存只有 {memory_gb:.1f} GB，编译可能失败，建议先加 swap。",
                    "warn",
                )

        config_text = render_config(mode_key, values)
        remote_path = remote_config_path(mode_key, ssh)

        _status(notify, "正在上传配置…")
        _ssh_run(client, f"mkdir -p {ssh.home}/open-xiaoai/{mode_key}", notify)
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as handle:
                handle.write(config_text)
        finally:
            sftp.close()
        _log(notify, f"配置已上传：{remote_path}", "ok")

        if method == "source":
            _status(notify, "正在本机准备源码…")
            base = Path(base_dir) if base_dir else Path(__file__).resolve().parent / "deploy"
            repo = fetch_source_bundle(base, mirror, custom_prefix, notify)
            _status(notify, "正在上传源码并构建…")
            image = build_remote_image(client, mode_key, ssh, repo, notify)
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


def client_commands(url: str) -> str:
    """给音箱端 Client 用的命令（见 packages/client-rust/README.md）。"""
    return (
        f"mkdir -p /data/open-xiaoai\n"
        f"echo '{url}' > /data/open-xiaoai/server.txt\n"
        f"curl -sSfL https://gitee.com/idootop/artifacts/releases/download/open-xiaoai-client/init.sh | sh"
    )
