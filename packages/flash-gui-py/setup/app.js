/* 初始化页：检测环境并触发安装 */

const $ = (selector) => document.querySelector(selector);
let launched = false;
let toolUrl = "https://androidmtk.com/download-amlogic-flash-tool";

async function api(path, body) {
  const options = { method: body === undefined ? "GET" : "POST" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  return response.json();
}

function setDot(selector, state) {
  const node = $(selector);
  node.className = "dot" + (state === true ? " ok" : state === false ? " err" : "");
}

function taskLabel(status) {
  if (status.running === "python-download") {
    const progress = status.installer && status.installer.progress ? ` ${status.installer.progress}%` : "";
    return `正在下载 Python${progress}…`;
  }
  if (status.running === "python") return "正在安装 Python…";
  if (status.running === "deps") return "正在安装界面依赖…";
  return "";
}

function render(status) {
  toolUrl = status.tool.url || toolUrl;
  $("#setup-status").textContent = status.running
    ? taskLabel(status)
    : status.ready
      ? "环境已就绪，可以启动控制台。"
      : "请先完成缺失项。";

  if (status.python.ok) {
    setDot("#python-dot", true);
    $("#python-detail").textContent = `已安装 ${status.python.version}：${status.python.path}`;
    $("#btn-python").textContent = "已安装";
    $("#btn-python").disabled = true;
    $("#btn-pick-python").textContent = "重新选择";
    $("#python-path").classList.add("hidden");
  } else {
    setDot("#python-dot", false);
    const error = status.installer && status.installer.error ? `（${status.installer.error}）` : "";
    $("#python-detail").textContent =
      "未检测到 Python。可以自动下载安装，或手动选择已有的 python.exe。" + error;

    const installDir = status.installer && status.installer.install_dir;
    const defaultDir = (status.installer && status.installer.default_dir) || "";
    $("#python-path").classList.remove("hidden");
    $("#python-dir").textContent = installDir || `${defaultDir}（默认）`;
    $("#btn-python-dir").textContent = installDir ? "恢复默认" : "更改";

    if (status.installer && status.installer.downloading) {
      $("#btn-python").textContent = "下载中…";
      $("#btn-python").disabled = true;
    } else {
      $("#btn-python").textContent =
        status.installer && status.installer.available ? "自动安装" : "下载并安装";
      $("#btn-python").disabled = Boolean(status.running);
    }
    $("#btn-pick-python").textContent = "手动选择";
  }
  $("#btn-pick-python").disabled = Boolean(status.running);

  if (status.deps.ok) {
    setDot("#deps-dot", true);
    $("#deps-detail").textContent = "pywebview、paramiko 已就绪";
    $("#btn-deps").textContent = "已安装";
    $("#btn-deps").disabled = true;
  } else if (!status.python.ok) {
    setDot("#deps-dot", null);
    $("#deps-detail").textContent = "需要先安装 Python";
    $("#btn-deps").textContent = "安装依赖";
    $("#btn-deps").disabled = true;
  } else {
    setDot("#deps-dot", false);
    $("#deps-detail").textContent = status.deps.message || "缺少界面依赖";
    $("#btn-deps").textContent = "安装依赖";
    $("#btn-deps").disabled = Boolean(status.running);
  }

  if (status.tool.ok) {
    setDot("#tool-dot", true);
    $("#tool-detail").textContent =
      status.tool.source === "zip"
        ? `已从 tools\\${status.tool.zip} 解压并找到 update.exe`
        : "已找到内置的 update.exe，刷机页会自动使用";
  } else if (status.tool.zip && status.tool.message) {
    setDot("#tool-dot", false);
    $("#tool-detail").textContent = `内置压缩包 ${status.tool.zip}：${status.tool.message}`;
  } else {
    setDot("#tool-dot", null);
    $("#tool-detail").textContent =
      "未找到（不影响启动）。点「下载工具」从官网下载 zip，放进 tools\\ 后点「重新检测」。";
  }

  $("#btn-launch").disabled = !status.ready || Boolean(status.running);
}

async function refresh() {
  if (launched) return;
  try {
    const status = await api("/api/status");
    render(status);
    if (status.running) loadLog();
  } catch (error) {
    $("#setup-status").textContent = "与本地服务的连接断开了，请重新启动控制台。";
  }
}

async function loadLog() {
  try {
    const data = await api("/api/log");
    const box = $("#log");
    if (data.text) {
      box.textContent = data.text;
      box.scrollTop = box.scrollHeight;
    }
  } catch (_) { /* ignore */ }
}

async function action(path, body = {}) {
  const result = await api(path, body);
  if (result.message && !result.ok) {
    alert(result.message);
  }
  await refresh();
  return result;
}

$("#btn-python").addEventListener("click", () => action("/api/install-python"));
$("#btn-pick-python").addEventListener("click", () => action("/api/pick-python"));
$("#btn-python-dir").addEventListener("click", async (event) => {
  event.preventDefault();
  const status = await api("/api/status");
  if (status.installer && status.installer.install_dir) {
    await action("/api/reset-python-dir");
  } else {
    await action("/api/pick-python-dir");
  }
});
$("#btn-deps").addEventListener("click", () => action("/api/install-deps"));
$("#btn-tool-download").addEventListener("click", () => window.open(toolUrl, "_blank"));
$("#btn-tools").addEventListener("click", () => action("/api/open-tools"));
$("#btn-refresh").addEventListener("click", async () => {
  const status = await api("/api/refresh", {});
  render(status);
});
$("#btn-exit").addEventListener("click", async () => {
  await api("/api/quit", {});
  launched = true;
  $("#setup-status").textContent = "已退出，可以关闭这个页面。";
});
$("#btn-launch").addEventListener("click", async () => {
  const result = await action("/api/launch");
  if (result.ok) {
    launched = true;
    $("#setup-status").textContent = "控制台已启动，可以关闭这个页面。";
    $("#btn-launch").disabled = true;
  }
});

refresh();
setInterval(refresh, 2000);
