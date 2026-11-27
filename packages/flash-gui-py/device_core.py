"""音箱侧运行时管理：XiaoAI-Plus（豆包实时语音）与 Home Mate（小瑞核心）。

两者都不在控制台本机运行：
- XiaoAI-Plus：通过 SSH 管理音箱上的 /data/xiaoai-plus（配置、进程、日志、自启）。
- Home Mate：通过 SSH 在 Docker 主机上部署 hm-core，管理界面是它自带的 :8930 控制台。
"""

from __future__ import annotations

import configparser
import hashlib
import io
import shlex
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path

import deploy_core as dcore

# ---------------- XiaoAI-Plus ----------------

XP_DIR = "/data/xiaoai-plus"
XP_BIN = f"{XP_DIR}/xiaoai_plus_speaker"
XP_CONFIG = f"{XP_DIR}/config.ini"
XP_LOG = f"{XP_DIR}/xiaoai_plus.log"
XP_KEYWORDS = f"{XP_DIR}/assets/keywords.txt"
XP_INSTALL_URL = "https://fastly.jsdelivr.net/gh/kslr/xiaoai-plus@main/install.sh"
XP_BOOT_URL = "https://fastly.jsdelivr.net/gh/kslr/xiaoai-plus@main/boot.sh"
XP_ARCHIVE_REMOTE = "/tmp/xiaoai-plus-oh2p.tar.gz"
XP_ARCHIVE_URLS = (
    ("GitHub Release", "https://github.com/MiKoYi/open-xiaoai-console/releases/download/v0.1.0/oh2p.tar.gz"),
    ("ghfast 镜像", "https://ghfast.top/https://github.com/MiKoYi/open-xiaoai-console/releases/download/v0.1.0/oh2p.tar.gz"),
    ("ghproxy 镜像", "https://ghproxy.net/https://github.com/MiKoYi/open-xiaoai-console/releases/download/v0.1.0/oh2p.tar.gz"),
)
XP_ARCHIVE_SHA256 = "3bfa7b55ac8cd2b72e76a6a86a29c3caec95a8fde6ff2d279747d104e32e969d"
XP_ARCHIVE_MIN_SIZE = 1_000_000
XP_WAKE_DEFAULT = "豆包豆包"
XP_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "xiaoai-plus"
XP_KEYWORDS = f"{XP_DIR}/keywords/keywords_doubao_repeat_end.txt"

# (section, key, label, default, secret, multiline)
XP_FIELDS = (
    ("realtime", "protocol", "协议版本（o2 / v3）", "o2", False, False),
    ("realtime", "playmate", "儿童英语玩伴（on / off）", "off", False, False),
    ("realtime", "api_key", "API Key", "", True, False),
    ("realtime", "model", "模型版本", "1.2.6.1", False, False),
    ("realtime", "voice", "音色", "zh_female_vv_uranus_bigtts", False, False),
    ("realtime", "bot_name", "机器人名称", "豆包", False, False),
    ("realtime", "system_role", "系统提示词", "你是一个中文语音助手，请始终用中文回答，除非用户明确要求英文。", False, True),
    ("realtime", "speaking_style", "说话风格", "语气自然、友好、简洁，每次不超过两句话。", False, True),
    ("wakeup", "say_hello", "唤醒回应", "我在，想聊点什么？", False, False),
    ("wakeup", "kws_threshold", "唤醒灵敏度", "0.10", False, False),
    ("wakeup", "kws_agc", "唤醒自动增益（on / off）", "on", False, False),
    ("wakeup", "exit_playback_threshold", "退出词阈值", "0.45", False, False),
    ("conversation", "idle_seconds", "会话空闲秒数", "30", False, False),
    ("conversation", "smart_turn", "智能断句（on / off）", "on", False, False),
    ("conversation", "duplex_mode", "双工模式（half / full）", "half", False, False),
    ("memory", "enabled", "记忆后台（on / off）", "off", False, False),
    ("memory", "child_names", "孩子英文名（逗号分隔，记忆用）", "", False, False),
    ("audio", "playback_gain", "播放增益", "0.90", False, False),
)

XP_CONFIG_TEMPLATE = """[realtime]
# Switch to v3 and restart speaker to roll back without replacing the binary.
protocol = o2
# off：使用下面的 system_role / speaking_style；on：内置儿童英语提示词
playmate = off
api_key = your_api_key
model = 1.2.6.1
voice = zh_female_vv_uranus_bigtts
bot_name = 豆包
system_role = 你是一个中文语音助手，请始终用中文回答，除非用户明确要求英文。
speaking_style = 语气自然、友好、简洁，每次不超过两句话。

[realtime.o2]
end_smooth_window_ms = 6000
voice = zh_female_vv_uranus_bigtts

[wakeup]
kws_agc = on
say_hello = 我在，想聊点什么？
keywords_file = /data/xiaoai-plus/keywords/keywords_doubao_repeat_end.txt
s32_shift = 16
input_channel = beamformed
kws_threshold = 0.10
kws_num_threads = 2
exit_playback_threshold = 0.45

[conversation]
idle_seconds = 30
smart_turn = on
smart_turn_complete_ms = 1500
smart_turn_open_ms = 4500
absolute_timeout_seconds = 0
duplex_mode = half
echo_canceller = apm
daily_token_limit = 0

[audio]
playback_gain = 0.90
dump_output_audio_raw = 0

[memory]
enabled = off
child_names = 

[stop_key]
device =
key_code = -1
"""


def _kv(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (output or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _read_ini(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # 保留 key 大小写
    try:
        parser.read_string(text or "")
    except configparser.Error:
        parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        parser.optionxform = str
    return parser


def xp_values(text: str) -> dict[str, str]:
    """把 config.ini 解析成表单值（只取已知字段，缺省用默认值）。"""
    parser = _read_ini(text)
    values: dict[str, str] = {}
    for section, key, _label, default, _secret, _multiline in XP_FIELDS:
        if key == "api_key":
            values[key] = _first_option(parser, "realtime", ("api_key", "apikey", "app_id"), default)
        elif parser.has_section(section) and parser.has_option(section, key):
            values[key] = parser.get(section, key)
        else:
            values[key] = default
    return values


def _first_option(parser: configparser.ConfigParser, section: str, keys, default: str) -> str:
    if not parser.has_section(section):
        return default
    for key in keys:
        if parser.has_option(section, key):
            value = parser.get(section, key).strip()
            if value:
                return value
    return default


def _wake_path_from_text(text: str) -> str:
    parser = _read_ini(text)
    if parser.has_section("wakeup") and parser.has_option("wakeup", "keywords_file"):
        value = parser.get("wakeup", "keywords_file").strip()
        if value:
            return value
    return XP_KEYWORDS


def xp_render_config(values: dict[str, str], existing: str = "") -> str:
    """在原有配置上更新已知字段，保留用户自定义的其它段落/键。"""
    parser = _read_ini(existing)
    if not parser.has_section("realtime"):
        parser.add_section("realtime")

    # 旧版字段全部移除
    for legacy in ("app_id", "access_token", "secret_key", "apikey", "x_connect_id", "x_reserved"):
        if parser.has_option("realtime", legacy):
            parser.remove_option("realtime", legacy)

    for section, key, _label, default, _secret, _multiline in XP_FIELDS:
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, str(values.get(key, default)))

    if not parser.has_section("wakeup"):
        parser.add_section("wakeup")
    if not parser.get("wakeup", "keywords_file", fallback="").strip():
        parser.set("wakeup", "keywords_file", XP_KEYWORDS)
    if parser.has_section("realtime.o2") and parser.has_option("realtime.o2", "voice"):
        parser.set("realtime.o2", "voice", str(values.get("voice", "")))

    buffer = io.StringIO()
    parser.write(buffer, space_around_delimiters=True)
    return buffer.getvalue()


def xp_wake_label(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if "@" in line:
            return line.rsplit("@", 1)[1].strip()
    return ""


def xp_generate_keywords(text: str) -> str:
    """用 sherpa-onnx 把唤醒词编码成 keywords.txt。"""
    phrase = (text or "").strip()
    if not phrase:
        raise dcore.DeployError("请填写唤醒词")
    if len(phrase) > 20:
        raise dcore.DeployError("唤醒词太长（最多 20 个字符）")
    try:
        import click
        from sherpa_onnx.cli import cli
    except ImportError as exc:
        raise dcore.DeployError(
            "生成唤醒词需要 sherpa-onnx：请在控制台执行\n"
            "  pip install sherpa-onnx pypinyin sentencepiece click"
        ) from exc

    tokens = XP_ASSETS_DIR / "tokens.txt"
    lexicon = XP_ASSETS_DIR / "en.phone"
    if not tokens.is_file() or not lexicon.is_file():
        raise dcore.DeployError(
            "缺少唤醒词资源文件：assets/xiaoai-plus/tokens.txt 或 en.phone"
        )

    with tempfile.TemporaryDirectory() as temp:
        raw = Path(temp) / "keywords_raw.txt"
        out = Path(temp) / "keywords.txt"
        raw.write_text(f"{phrase} @{phrase}\n", encoding="utf-8")
        args = [
            "text2token",
            "--tokens",
            str(tokens),
            "--tokens-type",
            "phone+ppinyin",
            "--lexicon",
            str(lexicon),
            str(raw),
            str(out),
        ]
        try:
            cli.main(args=args, prog_name="sherpa-onnx-cli", standalone_mode=False)
        except click.ClickException as exc:
            raise dcore.DeployError(f"生成唤醒词失败：{exc.format_message()}") from exc
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise dcore.DeployError("生成唤醒词失败") from exc
        if not out.is_file():
            raise dcore.DeployError("生成唤醒词失败：没有输出文件")
        content = out.read_text(encoding="utf-8").strip()

    if not content:
        raise dcore.DeployError("生成唤醒词失败：输出为空")
    return content + "\n"


def _xp_wake_line(phrase: str) -> str:
    generated = xp_generate_keywords(phrase).strip()
    tokens = generated.rsplit(" @", 1)[0] if " @" in generated else generated
    return f"{tokens} #0.05 @{phrase}"


def xp_render_keywords(phrase: str, existing_text: str = "") -> str:
    """只替换唤醒词那一行，保留后面的退出词（再见 / 别说了 ...）。"""
    wake = _xp_wake_line(phrase)
    lines = [line for line in (existing_text or "").splitlines() if line.strip()]
    rest = lines[1:] if lines else []
    return "\n".join([wake] + rest).rstrip() + "\n"


def xp_status(ssh, notify=None) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        _code, output = dcore._ssh_run(
            client,
            f"test -x {XP_BIN} && echo installed=1 || echo installed=0; "
            f"test -f {XP_CONFIG} && echo config=1 || echo config=0; "
            f"if [ -f {XP_LOG} ]; then echo log_size=$(wc -c < {XP_LOG}); else echo log_size=0; fi; "
            "echo running=$(ps | grep '[x]iaoai_plus_speaker' | wc -l); "
            "echo disk_kb=$(df -k /data | awk 'NR==2 {print $4}'); "
            "echo self_start=$(test -x /data/init.sh && echo 1 || echo 0)",
            quiet=True,
        )
        cfg_code, cfg_text = dcore._ssh_run(client, f"cat {XP_CONFIG}", quiet=True)
        wake_path = _wake_path_from_text(cfg_text if cfg_code == 0 else "")
        kw_code, kw_text = dcore._ssh_run(
            client, f"cat {shlex.quote(wake_path)}", quiet=True
        )
    finally:
        client.close()
    data = _kv(output)
    return {
        "installed": data.get("installed") == "1",
        "config": data.get("config") == "1",
        "running": (data.get("running") or "0").isdigit() and int(data.get("running", "0")) > 0,
        "log_size": int(data.get("log_size") or 0),
        "disk_kb": int(data.get("disk_kb") or 0),
        "self_start": data.get("self_start") == "1",
        "values": xp_values(cfg_text if cfg_code == 0 else ""),
        "wake_word": xp_wake_label(kw_text if kw_code == 0 else ""),
        "keywords_file": wake_path,
    }


def xp_read_config(ssh) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        code, text = dcore._ssh_run(client, f"cat {XP_CONFIG}", quiet=True)
        wake_path = _wake_path_from_text(text if code == 0 else "")
        kw_code, kw_text = dcore._ssh_run(
            client, f"cat {shlex.quote(wake_path)}", quiet=True
        )
    finally:
        client.close()
    text = text if code == 0 else ""
    return {
        "text": text,
        "values": xp_values(text),
        "wake_word": xp_wake_label(kw_text if kw_code == 0 else ""),
        "keywords_file": wake_path,
    }


def xp_save_config(ssh, values: dict[str, str], wake_word: str = "", notify=None) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        code, existing = dcore._ssh_run(client, f"cat {XP_CONFIG}", quiet=True)
        rendered = xp_render_config(values, existing if code == 0 else "")
        dcore._ssh_run(client, f"mkdir -p {XP_DIR}", quiet=True)
        dcore._upload_text(client, XP_CONFIG, rendered, notify)
        dcore._ssh_run(client, f"chmod 0600 {XP_CONFIG}", quiet=True)
        dcore._log(notify, f"已写入 {XP_CONFIG}", "ok")

        phrase = (wake_word or "").strip()
        wake_path = _wake_path_from_text(rendered)
        if phrase:
            kw_code, existing_kw = dcore._ssh_run(
                client, f"cat {shlex.quote(wake_path)}", quiet=True
            )
            keywords = xp_render_keywords(phrase, existing_kw if kw_code == 0 else "")
            remote_dir = wake_path.rsplit("/", 1)[0] if "/" in wake_path else XP_DIR
            dcore._ssh_run(client, f"mkdir -p {shlex.quote(remote_dir)}", quiet=True)
            dcore._upload_text(client, wake_path, keywords, notify)
            dcore._log(notify, f"已更新唤醒词：{phrase}", "ok")
    finally:
        client.close()
    return {"path": XP_CONFIG, "wake_word": (wake_word or "").strip()}


def _xp_stop_command() -> str:
    return (
        "if [ -f " + XP_DIR + "/guardian.pid ]; then p=$(cat " + XP_DIR + "/guardian.pid); "
        'kill "$p" 2>/dev/null || true; rm -f ' + XP_DIR + "/guardian.pid; fi; "
        "pids=$(ps | grep '[x]iaoai_plus_speaker' | awk '{print $1}'); "
        'if [ -n "$pids" ]; then kill $pids; sleep 1; fi; echo stopped'
    )


def _xp_start_command() -> str:
    # 优先用官方 boot.sh（带守护/日志轮转）；必须用绝对路径，boot.sh 靠 cmdline 识别已有守护
    return (
        f"if [ -x {XP_DIR}/boot.sh ]; then /bin/sh {XP_DIR}/boot.sh >/dev/null 2>&1; "
        f"else {XP_DIR}/xiaoai_plus_speaker -c {XP_CONFIG} >> {XP_LOG} 2>&1 < /dev/null & fi; "
        "sleep 4; "
        "if ps | grep -q '[x]iaoai_plus_speaker'; then echo RUNNING=1; "
        f"else echo RUNNING=0; tail -n 20 {XP_LOG}; fi"
    )


def xp_control(ssh, action: str, notify=None) -> dict:
    if action not in ("start", "stop", "restart"):
        raise dcore.DeployError(f"未知的 XiaoAI-Plus 操作：{action}")
    client = dcore._ssh_connect(ssh)
    try:
        if action in ("stop", "restart"):
            code, output = dcore._ssh_run(client, _xp_stop_command(), notify)
            if code != 0:
                raise dcore.DeployError(f"停止失败：{output.strip() or code}")
        if action in ("start", "restart"):
            code, output = dcore._ssh_run(client, _xp_start_command(), notify)
            if code != 0 or "RUNNING=1" not in output:
                detail = output.strip() or f"退出码 {code}"
                raise dcore.DeployError(f"启动失败：进程没有保持运行。\n{detail}")
    finally:
        client.close()
    _log_action(notify, action)
    return {"action": action}


def _log_action(notify, action: str) -> None:
    text = {"start": "已启动", "stop": "已停止", "restart": "已重启"}.get(action, action)
    dcore._log(notify, f"XiaoAI-Plus {text}", "ok")


def xp_install(ssh, notify=None) -> dict:
    """安装 / 更新：控制台下载官方包并打上 API Key 补丁，再上传安装。"""
    return xp_offline_install(ssh, "", notify)


def xp_logs(ssh, lines: int = 120) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        _code, output = dcore._ssh_run(client, f"tail -n {int(lines)} {XP_LOG}", quiet=True)
    finally:
        client.close()
    return {"text": output}


def xp_archive_ok(path: Path | str) -> bool:
    archive = Path(path)
    try:
        if archive.stat().st_size < XP_ARCHIVE_MIN_SIZE:
            return False
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError):
        return False
    return "oh2p/xiaoai_plus_speaker" in names


def xp_archive_cache() -> Path:
    return Path(__file__).resolve().parent / "deploy" / "xiaoai-plus" / "oh2p.tar.gz"


def xp_download_archive(notify=None, force: bool = False) -> Path:
    """在控制台本机下载 XiaoAI-Plus 安装包（音箱不需要外网）。"""
    target = xp_archive_cache()
    if not force and target.is_file() and xp_archive_ok(target):
        dcore._log(notify, f"使用已下载的安装包：{target}")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for label, url in XP_ARCHIVE_URLS:
        for attempt in range(2):
            try:
                if attempt == 0:
                    dcore._log(notify, f"正在从 {label} 下载安装包…")
                else:
                    dcore._log(notify, f"{label} 下载失败，重试一次…", "warn")
                request = urllib.request.Request(url, headers={"User-Agent": "open-xiaoai-console"})
                with urllib.request.urlopen(request, timeout=90) as response, open(target, "wb") as handle:
                    total = int(response.headers.get("Content-Length") or 0)
                    downloaded = 0
                    last_percent = -1
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = int(downloaded * 100 / total)
                            if percent >= last_percent + 10:
                                last_percent = percent
                                dcore._log(notify, f"下载安装包… {percent}%")
                if xp_archive_ok(target):
                    if XP_ARCHIVE_SHA256:
                        digest = hashlib.sha256(target.read_bytes()).hexdigest()
                        if digest != XP_ARCHIVE_SHA256:
                            errors.append(f"{label}：SHA-256 不匹配")
                            target.unlink(missing_ok=True)
                            break
                    dcore._log(notify, f"安装包已下载：{target}", "ok")
                    return target
                errors.append(f"{label}：下载到的文件不是有效安装包")
                target.unlink(missing_ok=True)
                break
            except Exception as exc:  # noqa: BLE001 - 网络错误类型很多
                target.unlink(missing_ok=True)
                if attempt == 1:
                    errors.append(f"{label}：{exc}")

    raise dcore.DeployError(
        "下载 XiaoAI-Plus 安装包失败：\n- " + "\n- ".join(errors)
        + "\n可以在电脑浏览器里下载 oh2p.tar.gz，然后在页面里用「浏览…」选择离线包安装。"
    )


def _patch_xp_binary(data: bytes) -> bytes:
    """把官方二进制的旧鉴权头改成 X-Api-Key；豆包版则把内置英文提示词改成中文。"""
    if b"UPX!" in data[:0x10000]:
        # 已用 UPX 压缩的源码编译版，鉴权/中文提示词在编译期已确定，不能再改。
        return data
    if b"X-Api-Key\x00" in data:
        # xiaoai-doubao 内置的是儿童英语玩伴提示词；等长替换成中文指令
        if b"English" in data:
            return data.replace(b"English", b"Chinese")
        return data
    blob = bytearray(data)

    header = b"X-Api-App-ID\x00"
    index = blob.find(header)
    if index < 0:
        raise dcore.DeployError("安装包里的程序无法打补丁：找不到 X-Api-App-ID")
    blob[index:index + len(header)] = b"X-Api-Key\x00\x00\x00\x00"

    index = blob.find(b"app_id\x00\x00realtime.app_id")
    if index < 0:
        raise dcore.DeployError("安装包里的程序无法打补丁：找不到 app_id 配置键")
    blob[index:index + 7] = b"apikey\x00"

    index = blob.find(b"access_token\x00\x00\x00\x00realtime.access_token")
    if index < 0:
        raise dcore.DeployError("安装包里的程序无法打补丁：找不到 access_token 配置键")
    blob[index:index + 13] = b"x_connect_id\x00"

    index = blob.find(b"secret_key\x00\x00realtime.secret_key")
    if index < 0:
        raise dcore.DeployError("安装包里的程序无法打补丁：找不到 secret_key 配置键")
    blob[index:index + 11] = b"x_reserved\x00"

    return bytes(blob)


def xp_patch_archive(path: Path) -> Path:
    """返回打过补丁的安装包（原包不动）。"""
    with tarfile.open(path, "r:gz") as tar:
        names = set(tar.getnames())
        binary = tar.extractfile("oh2p/xiaoai_plus_speaker").read()
        example = tar.extractfile("oh2p/config.ini.example").read()

    patched_binary = _patch_xp_binary(binary)
    example_new = XP_CONFIG_TEMPLATE.encode("utf-8")
    missing = [
        item
        for item in sorted(XP_ASSETS_DIR.iterdir())
        if item.is_file() and f"oh2p/assets/{item.name}" not in names
    ]
    if patched_binary == binary and example == example_new and not missing:
        return path

    target = path.with_name("oh2p.patched.tar.gz")
    with tarfile.open(path, "r:gz") as tin, tarfile.open(target, "w:gz") as tout:
        for member in tin.getmembers():
            if member.name == "oh2p/xiaoai_plus_speaker":
                data = patched_binary
            elif member.name == "oh2p/config.ini.example":
                data = example_new
            elif member.isfile():
                data = tin.extractfile(member).read()
            else:
                tout.addfile(member)
                continue
            member.size = len(data)
            tout.addfile(member, io.BytesIO(data))
        for item in missing:
            data = item.read_bytes()
            info = tarfile.TarInfo(f"oh2p/assets/{item.name}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tout.addfile(info, io.BytesIO(data))
    return target


def _xp_offline_script() -> str:
    return (
        "set -e\n"
        "avail=$(df -k /data | awk 'NR==2 {print $4}')\n"
        "if [ -n \"$avail\" ] && [ \"$avail\" -lt 61440 ]; then echo '/data 磁盘空间不足：至少需要 60MB'; exit 1; fi\n"
        "rm -rf /data/.xiaoai-plus-install /data/.xiaoai-plus-oh2p.tar.gz && mkdir -p /data/.xiaoai-plus-install\n"
        f"tar -xzf {XP_ARCHIVE_REMOTE} -C /data/.xiaoai-plus-install\n"
        f"mkdir -p {XP_DIR}/assets {XP_DIR}/keywords\n"
        f"cp /data/.xiaoai-plus-install/oh2p/xiaoai_plus_speaker {XP_BIN}\n"
        f"if [ -f /data/.xiaoai-plus-install/oh2p/boot.sh ]; then cp /data/.xiaoai-plus-install/oh2p/boot.sh {XP_DIR}/boot.sh; fi\n"
        f"cp -f /data/.xiaoai-plus-install/oh2p/assets/* {XP_DIR}/assets/\n"
        f"if [ ! -f {XP_DIR}/keywords/keywords_doubao_repeat_end.txt ] && [ -d /data/.xiaoai-plus-install/oh2p/keywords ]; then cp /data/.xiaoai-plus-install/oh2p/keywords/* {XP_DIR}/keywords/; fi\n"
        f"chmod 0755 {XP_BIN}\n"
        f"if [ -f {XP_DIR}/boot.sh ]; then chmod 0755 {XP_DIR}/boot.sh; fi\n"
        f"chmod 0644 {XP_DIR}/assets/* {XP_DIR}/keywords/*\n"
        f"if [ ! -f {XP_CONFIG} ]; then\n"
        f"cat > {XP_CONFIG} <<'XIAOAI_CONFIG_EOF'\n"
        f"{XP_CONFIG_TEMPLATE}"
        "XIAOAI_CONFIG_EOF\n"
        f"  chmod 0600 {XP_CONFIG}\n"
        "fi\n"
        f"rm -rf /data/.xiaoai-plus-install {XP_ARCHIVE_REMOTE}\n"
        "echo OFFLINE_INSTALL_OK"
    )


def xp_offline_install(ssh, archive: str = "", notify=None) -> dict:
    """离线安装：控制台本机准备安装包，上传到音箱后本地解压安装。"""
    if archive:
        source = Path(archive)
        if not source.is_file():
            raise dcore.DeployError(f"离线安装包不存在：{archive}")
        if not xp_archive_ok(source):
            raise dcore.DeployError(
                "这个文件不是有效的 XiaoAI-Plus 安装包（需要包含 oh2p/xiaoai_plus_speaker）。"
            )
    else:
        source = xp_download_archive(notify)

    patched = xp_patch_archive(source)
    client = dcore._ssh_connect(ssh)
    try:
        size_mb = patched.stat().st_size / 1024 / 1024
        dcore._log(notify, f"正在上传安装包（{size_mb:.1f} MB）…")
        last_percent = -1

        def _progress(sent: int, total: int) -> None:
            nonlocal last_percent
            if not total:
                return
            percent = int(sent * 100 / total)
            if percent >= last_percent + 20:
                last_percent = percent
                dcore._log(notify, f"上传安装包… {percent}%")
                dcore._status(notify, f"正在上传安装包… {percent}%")

        dcore._upload_file(client, patched, XP_ARCHIVE_REMOTE, notify, _progress)
        code, output = dcore._ssh_run(client, _xp_offline_script(), notify)
        if code != 0:
            raise dcore.DeployError(
                "离线安装失败：\n" + (dcore._tail_lines(output) or f"退出码 {code}")
            )
    finally:
        client.close()
        if patched != source:
            patched.unlink(missing_ok=True)
    dcore._log(notify, "XiaoAI-Plus 安装完成（API Key 版）", "ok")
    return {"installed": True, "archive": str(source)}


def xp_enable_autostart(ssh, notify=None) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        command = (
            "if [ -f /data/init.sh ]; then cp -n /data/init.sh /data/init.sh.bak 2>/dev/null || true; fi; "
            f"if [ -x {XP_DIR}/boot.sh ]; then cp -f {XP_DIR}/boot.sh /data/init.sh; "
            f"else curl -fsSL {XP_BOOT_URL} -o /data/init.sh; fi; "
            "chmod 0755 /data/init.sh && echo ok"
        )
        code, output = dcore._ssh_run(client, command, notify)
        if code != 0:
            raise dcore.DeployError(f"写入开机自启失败：{dcore._tail_lines(output) or code}")
        dcore._log(notify, "已写入 /data/init.sh（旧文件备份为 init.sh.bak）", "ok")
    finally:
        client.close()
    return {"self_start": True}


# ---------------- Home Mate ----------------

HM_IMAGE = "ghcr.io/yes999zc/home-mate:latest"
HM_CONTAINER = "home-mate-core"
HM_CONSOLE_PORT = 8930


def hm_check(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except Exception:  # noqa: BLE001 - 网络错误类型很多
        return False


def _sudo_run(client, command: str, password: str, notify=None):
    """用 sudo 执行命令，密码通过 stdin 传入（不出现在进程参数里）。"""
    if not password:
        raise dcore.DeployError("需要 sudo 密码才能操作 Docker。")
    wrapped = f"sudo -S -p '' sh -c {shlex.quote(command)}"
    dcore._log(notify, f"[远程] $ sudo sh -c {command}")
    stdin, stdout, _ = client.exec_command(f"exec 2>&1; {wrapped}")
    try:
        stdin.write((password + "\n").encode("utf-8"))
        stdin.flush()
    finally:
        try:
            stdin.channel.shutdown_write()
        except Exception:  # noqa: BLE001
            pass
    lines: list[str] = []
    for line in stdout:
        line = line.rstrip()
        lines.append(line)
        if line:
            dcore._log(notify, line)
    return stdout.channel.recv_exit_status(), "\n".join(lines)


def _hm_command(data_dir: str, node_id: str) -> str:
    return (
        f"mkdir -p {shlex.quote(data_dir)} && "
        f"chown -R 1000:1001 {shlex.quote(data_dir)} && "
        f"docker rm -f {HM_CONTAINER} >/dev/null 2>&1 || true; "
        f"docker run -d --name {HM_CONTAINER} --restart unless-stopped --network host "
        f"-e TZ=Asia/Shanghai -e HM_NODE_ID={shlex.quote(node_id)} -e HM_GATEWAY_ALLOW_LAN=1 "
        f"-v {shlex.quote(data_dir)}:/data {HM_IMAGE}"
    )


def hm_deploy(ssh, data_dir: str = "/opt/home-mate/data", node_id: str = "nas-core-1",
              use_sudo: bool = True, sudo_password: str = "", notify=None) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        command = _hm_command(data_dir, node_id)
        if use_sudo:
            code, output = _sudo_run(client, command, sudo_password, notify)
        else:
            code, output = dcore._ssh_run(client, command, notify)
        if code != 0:
            raise dcore.DeployError(
                "Home Mate 部署失败：\n" + (dcore._tail_lines(output) or f"退出码 {code}")
            )
        dcore._log(notify, "Home Mate 容器已启动，首次打开控制台需要设置访问令牌", "ok")
    finally:
        client.close()
    return {"container": HM_CONTAINER, "url": f"http://{ssh.host}:{HM_CONSOLE_PORT}"}


def hm_status(ssh, use_sudo: bool = False, sudo_password: str = "", notify=None) -> dict:
    client = dcore._ssh_connect(ssh)
    try:
        command = (
            f"docker inspect -f '{{{{.State.Status}}}}' {HM_CONTAINER} 2>/dev/null || echo missing; "
            f"docker logs --tail 20 {HM_CONTAINER} 2>&1 | tail -20"
        )
        if use_sudo:
            code, output = _sudo_run(client, command, sudo_password, notify=None)
        else:
            code, output = dcore._ssh_run(client, command, quiet=True)
    finally:
        client.close()
    lines = [line for line in output.splitlines() if line.strip()]
    state = lines[0].strip() if lines else "missing"
    return {
        "state": state,
        "running": state == "running",
        "logs": "\n".join(lines[1:]),
        "url": f"http://{ssh.host}:{HM_CONSOLE_PORT}",
    }
