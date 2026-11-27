/* Open-XiaoAI 控制台前端 */

const S = {
  data: null,
  settings: {},
  model: "LX06",
  busy: false,
  lastAction: "",
  ssh: null,
  env: null,
  ready: false,
  deployTab: "hm",
  configTab: "hm",
  hmUrl: "",
  xpValues: {},
};

window.addEventListener("error", (event) => {
  try {
    log(`前端错误：${event.message}`, "error");
  } catch (_) { /* ignore */ }
});

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const config = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (data.message) message = data.message;
    } catch (_) { /* ignore */ }
    throw new Error(message);
  }
  return response.json();
}

/* ---------------- 日志 ---------------- */

function log(text, level = "info") {
  const box = $("#log");
  const line = document.createElement("span");
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  line.className = level;
  line.textContent = `[${time}] ${text}\n`;
  box.appendChild(line);
  while (box.childNodes.length > 800) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function setStatus(text) { $("#status-text").textContent = text; }

async function copyLog() {
  const text = ($("#log").textContent || "").trim();
  if (!text) return toast("日志还是空的");
  try {
    await navigator.clipboard.writeText(text);
    toast("日志已复制到剪贴板");
    return;
  } catch (_) { /* 回退到 execCommand */ }
  const node = $("#log");
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(node);
  selection.removeAllRanges();
  selection.addRange(range);
  try {
    const ok = document.execCommand("copy");
    toast(ok ? "日志已复制到剪贴板" : "复制失败，请手动选择日志", !ok);
  } catch (_) {
    toast("复制失败，请手动选择日志", true);
  } finally {
    selection.removeAllRanges();
  }
}

function setBusy(busy) {
  S.busy = busy;
  $("#busy").classList.toggle("hidden", !busy);
  const ids = [
    "btn-flash", "btn-restore", "btn-identify", "btn-download-patched",
    "btn-download-official", "btn-patch", "btn-find-exe", "btn-driver",
    "btn-hm-connect", "btn-hm-open", "btn-hm-deploy", "btn-hm-status",
    "btn-xp-status", "btn-xp-install", "btn-xp-autostart", "btn-xp-save",
    "btn-xp-start", "btn-xp-stop", "btn-xp-restart", "btn-xp-logs", "btn-xp-offline",
  ];
  ids.forEach((id) => { const node = document.getElementById(id); if (node) node.disabled = busy; });
  $$("[data-pick]").forEach((node) => { node.disabled = busy; });
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), isError ? 6000 : 3500);
}

function askConfirm(message, options = {}) {
  return new Promise((resolve) => {
    const modal = $("#confirm-modal");
    $("#confirm-title").textContent = options.title || "请确认";
    $("#confirm-message").textContent = message;
    $("#confirm-ok").textContent = options.okText || "确定";
    $("#confirm-cancel").textContent = options.cancelText || "取消";
    $("#confirm-cancel").classList.toggle("hidden", Boolean(options.alert));
    modal.classList.remove("hidden");

    const finish = (value) => {
      modal.classList.add("hidden");
      $("#confirm-ok").onclick = null;
      $("#confirm-cancel").onclick = null;
      resolve(value);
    };
    $("#confirm-ok").onclick = () => finish(true);
    $("#confirm-cancel").onclick = () => finish(false);
  });
}

function askChoice(title, message, choices) {
  return new Promise((resolve) => {
    const modal = $("#choice-modal");
    $("#choice-title").textContent = title;
    $("#choice-message").textContent = message;
    const box = $("#choice-actions");
    box.innerHTML = "";
    choices.forEach((choice) => {
      const button = el("button", `btn${choice.primary ? " primary" : ""}`, choice.label);
      button.type = "button";
      button.addEventListener("click", () => {
        modal.classList.add("hidden");
        resolve(choice.value);
      });
      box.appendChild(button);
    });
    modal.classList.remove("hidden");
  });
}

/* ---------------- 设置 ---------------- */

let saveTimer = null;
function saveSettings() {
  S.settings.model = S.model;
  S.settings.image = $("#image-path").value;
  S.settings.exe = $("#exe-path").value;
  S.settings.speaker_ip = $("#speaker-ip").value.trim();
  S.settings.deploy_tab = S.deployTab;
  S.settings.config_tab = S.configTab;
  S.settings.hm_url = $("#hm-url").value.trim();
  S.settings.hm_host = $("#hm-host").value.trim();
  S.settings.hm_port = $("#hm-port").value.trim();
  S.settings.hm_user = $("#hm-user").value.trim();
  S.settings.hm_data = $("#hm-data").value.trim();
  S.settings.hm_node = $("#hm-node").value.trim();
  S.settings.hm_sudo = $("#hm-sudo").checked;
  S.settings.hm_pass = $("#hm-pass").value;
  S.settings.hm_sudo_pass = $("#hm-sudo-pass").value;
  S.settings.xp_host = $("#xp-host").value.trim();
  S.settings.xp_port = $("#xp-port").value.trim();
  S.settings.xp_user = $("#xp-user").value.trim();
  S.settings.xp_pass = $("#xp-pass").value;
  S.settings.xp_archive = $("#xp-archive").value.trim();
  S.settings.xp_wake_word = $("#xp-wake-word").value.trim();
  S.settings.xp_values = S.xpValues;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    api("/api/settings", { method: "POST", body: S.settings }).catch(() => {});
  }, 350);
}

/* ---------------- 机型 ---------------- */

function renderModels() {
  const box = $("#model-seg");
  box.innerHTML = "";
  S.data.models.forEach((model) => {
    const button = el("button", model.key === S.model ? "active" : "", model.name);
    button.type = "button";
    button.addEventListener("click", () => {
      S.model = model.key;
      renderModels();
      updateModelTip();
      updateImageInfo();
      updateDeployWarning();
      saveSettings();
    });
    box.appendChild(button);
  });
  updateModelTip();
}

function currentModel() {
  return S.data.models.find((item) => item.key === S.model) || S.data.models[0];
}

function updateModelTip() {
  const model = currentModel();
  $("#model-tip").textContent = `${model.name}：${model.connection}；当前支持固件版本 ${model.version}。`;
}

function updateImageInfo() {
  const path = $("#image-path").value.trim();
  const model = currentModel();
  const node = $("#image-info");
  if (!path) {
    node.className = "hint";
    node.textContent = `还没有选择固件。点「下载补丁固件」自动下载 ${model.filename}；或下载官方固件后点「制作补丁固件」。`;
    return;
  }
  const name = path.split(/[\\/]/).pop();
  node.className = "hint ok";
  node.textContent = `${name} ｜ ${model.name} 的固件`;
}

/* ---------------- 部署模式 ---------------- */

/* ---------------- 部署：Home Mate / XiaoAI-Plus ---------------- */

function parseJson(text) {
  try { return JSON.parse(text || "{}"); } catch (_) { return {}; }
}

const DEPLOY_SUPPORT = {
  hm: {
    models: ["LX06"],
    text: "Home Mate 支持小爱音箱 Pro（LX06）、Echo Dot 2、Android 网关，不支持 OH2P",
  },
  xp: {
    models: ["OH2P"],
    text: "XiaoAI-Plus 仅支持 Xiaomi 智能音箱 Pro（OH2P），LX06 等其它机型不支持",
  },
};

function switchDeployTab(tab) {
  S.deployTab = tab === "xp" ? "xp" : "hm";
  $$("#deploy-tabs .tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === S.deployTab);
  });
  $("#tab-hm").classList.toggle("hidden", S.deployTab !== "hm");
  $("#tab-xp").classList.toggle("hidden", S.deployTab !== "xp");
  updateDeployWarning();
  saveSettings();
}

function deploySupportWarning(tab = S.deployTab) {
  const support = DEPLOY_SUPPORT[tab];
  if (!support || support.models.includes(S.model)) return "";
  const model = S.data.models.find((item) => item.key === S.model);
  const name = model ? model.name : S.model;
  return `当前在刷机页选择的机型是「${name}」，${support.text}。`;
}

function updateDeployWarning() {
  const node = $("#deploy-warning");
  if (!node) return;
  const text = deploySupportWarning();
  node.textContent = text ? `⚠️ ${text}` : "";
  node.classList.toggle("hidden", !text);
}

async function confirmUnsupported(tab = S.deployTab) {
  const warning = deploySupportWarning(tab);
  if (!warning) return true;
  return askConfirm(`${warning}\n\n继续操作可能部署后无法使用，仍要继续吗？`, {
    title: "机型可能不支持",
    okText: "仍然继续",
  });
}

function switchConfigTab(tab) {
  S.configTab = tab === "xp" ? "xp" : "hm";
  $$("#config-tabs .tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.ctab === S.configTab);
  });
  $("#ctab-hm").classList.toggle("hidden", S.configTab !== "hm");
  $("#ctab-xp").classList.toggle("hidden", S.configTab !== "xp");
  saveSettings();
}

function normalizeUrl(value) {
  let url = String(value || "").trim();
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  return url.replace(/\/+$/, "");
}

function sshParams(prefix) {
  return {
    host: $(`#${prefix}-host`).value.trim(),
    port: Number($(`#${prefix}-port`).value || 22),
    user: $(`#${prefix}-user`).value.trim() || "root",
    password: $(`#${prefix}-pass`).value,
    key_path: "",
  };
}

function connectHomeMate() {
  const url = normalizeUrl($("#hm-url").value);
  if (!url) return toast("请填写 Home Mate 控制台地址", true);
  $("#hm-url").value = url;
  S.hmUrl = url;
  $("#hm-frame").src = url;
  $("#hm-hint").className = "hint";
  $("#hm-hint").textContent = `已加载 ${url}；如果下方空白或提示拒绝，请点「新窗口打开」。`;
  saveSettings();
}

async function deployHomeMate() {
  const ssh = sshParams("hm");
  if (!ssh.host) return toast("请填写主机地址", true);
  if (!ssh.password) return toast("请填写 SSH 密码", true);
  if (!(await confirmUnsupported("hm"))) return;
  const useSudo = $("#hm-sudo").checked;
  start("hm_deploy", {
    ssh,
    data_dir: $("#hm-data").value.trim() || "/opt/home-mate/data",
    node_id: $("#hm-node").value.trim() || "nas-core-1",
    use_sudo: useSudo,
    sudo_password: $("#hm-sudo-pass").value || (useSudo ? ssh.password : ""),
  });
}

function statusHomeMate() {
  const ssh = sshParams("hm");
  if (!ssh.host) return toast("请填写主机地址", true);
  start("hm_status", {
    ssh,
    use_sudo: $("#hm-sudo").checked,
    sudo_password: $("#hm-sudo-pass").value || ssh.password,
  });
}

function renderXpFields(values = null) {
  const box = $("#xp-fields");
  box.innerHTML = "";
  if (values) S.xpValues = { ...values };
  const saved = S.settings.xp_values || {};
  (S.data.xp_fields || []).forEach((field) => {
    const key = field.key;
    if (S.xpValues[key] === undefined) {
      S.xpValues[key] = saved[key] !== undefined ? saved[key] : (field.default || "");
    }
    const wrap = el("div", "field");
    if (field.multiline) wrap.classList.add("span-all");
    wrap.appendChild(el("label", "", field.label));
    if (field.multiline) {
      const input = el("textarea");
      input.value = S.xpValues[key];
      input.addEventListener("input", () => { S.xpValues[key] = input.value; saveSettings(); });
      wrap.appendChild(input);
    } else {
      const input = el("input");
      if (field.secret) input.type = "password";
      input.value = S.xpValues[key];
      input.addEventListener("input", () => { S.xpValues[key] = input.value; saveSettings(); });
      wrap.appendChild(input);
    }
    box.appendChild(wrap);
  });
}

function xpAction(action, extra = {}) {
  const ssh = sshParams("xp");
  if (!ssh.host) { toast("请填写音箱地址", true); return false; }
  start(action, { ssh, ...extra });
  return true;
}

function applyXpStatus(info) {
  const parts = [];
  parts.push(info.installed ? "已安装" : "未安装");
  parts.push(info.running ? "运行中" : "未运行");
  if (info.self_start) parts.push("开机自启");
  if (info.disk_kb) parts.push(`/data 剩余 ${Math.round(info.disk_kb / 1024)} MB`);
  if (info.wake_word) {
    parts.push(`唤醒词：${info.wake_word}`);
    if (!$("#xp-wake-word").value.trim()) $("#xp-wake-word").value = info.wake_word;
  }
  const hint = $("#xp-hint");
  hint.className = `hint ${info.installed && info.running ? "ok" : info.installed ? "warn" : ""}`;
  hint.textContent = parts.join(" · ");
}

async function saveXpConfig() {
  const values = { ...S.xpValues };
  if (!String(values.api_key || "").trim()) {
    toast("请填写 API Key（火山引擎豆包语音控制台获取）", true);
    return;
  }
  const wakeWord = $("#xp-wake-word").value.trim();
  if (!wakeWord) {
    toast("请填写唤醒词", true);
    return;
  }
  if (!(await confirmUnsupported("xp"))) return;
  xpAction("xp_save", { values, wake_word: wakeWord, restart: true });
}

function applySettings() {
  const st = S.settings;
  $("#speaker-ip").value = st.speaker_ip || "";
  $("#image-path").value = st.image || "";
  $("#exe-path").value = st.exe || "";

  S.deployTab = st.deploy_tab === "xp" ? "xp" : "hm";
  S.configTab = st.config_tab === "xp" ? "xp" : "hm";
  $("#hm-url").value = st.hm_url || "";
  $("#hm-host").value = st.hm_host || "";
  $("#hm-port").value = st.hm_port || 22;
  $("#hm-user").value = st.hm_user || "root";
  $("#hm-data").value = st.hm_data || "/opt/home-mate/data";
  $("#hm-node").value = st.hm_node || "nas-core-1";
  $("#hm-sudo").checked = st.hm_sudo !== false;
  $("#hm-sudo-row").classList.toggle("hidden", !$("#hm-sudo").checked);
  $("#hm-pass").value = st.hm_pass || "";
  $("#hm-sudo-pass").value = st.hm_sudo_pass || "";
  $("#xp-host").value = st.xp_host || st.speaker_ip || "";
  $("#xp-port").value = st.xp_port || 22;
  $("#xp-user").value = st.xp_user || "root";
  $("#xp-pass").value = st.xp_pass || "";
  $("#xp-archive").value = st.xp_archive || "";
  $("#xp-wake-word").value = st.xp_wake_word || "豆包豆包";
  updateImageInfo();
}

/* ---------------- 启动流程 ---------------- */

async function boot() {
  try {
    S.data = await api("/api/bootstrap");
  } catch (error) {
    log(`初始化失败：${error.message}`, "error");
    return;
  }

  try {
    S.settings = S.data.settings || {};
    S.model = S.settings.model || S.data.defaults.model;
    if (!S.data.models.some((item) => item.key === S.model)) S.model = S.data.defaults.model;

    applySettings();
    renderModels();
    renderXpFields();
    switchDeployTab(S.deployTab);
    switchConfigTab(S.configTab);

    if (!S.settings.exe && S.data.bundled_exe) {
      $("#exe-path").value = S.data.bundled_exe;
      $("#exe-info").className = "hint ok";
      if (S.data.tool_source === "zip") {
        $("#exe-info").textContent = `已从内置压缩包 ${S.data.tool_zip} 解压并找到刷机工具，无需另外下载。`;
      } else {
        $("#exe-info").textContent = "已找到内置刷机工具，无需另外下载。";
      }
    } else if (S.data.tool_error) {
      $("#exe-info").className = "hint warn";
      $("#exe-info").textContent = S.data.tool_error;
    } else if (S.settings.exe) {
      $("#exe-info").className = "hint ok";
      $("#exe-info").textContent = `当前工具：${S.settings.exe}`;
    }

    const github = $("#link-github");
    if (github) github.href = S.data.github_url;

    bindUI();
    await loadEnv();
    if (!location.search.includes("no-sse")) connectEvents();
    checkStatus();
    setInterval(checkStatus, 15000);
    const view = new URLSearchParams(location.search).get("view");
    if (view === "setup") {
      switchView("setup");
    } else if (S.ready) {
      switchView(view === "deploy" || view === "config" ? view : "flash");
    } else {
      switchView("setup");
    }
    log("控制台已就绪。");
  } catch (error) {
    log(`初始化失败：${error.message}`, "error");
  }
}

/* ---------------- 事件 ---------------- */

function bindUI() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  $$("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => pickFile(button.dataset.pick));
  });

  $("#image-path").addEventListener("change", () => { updateImageInfo(); saveSettings(); });
  $("#exe-path").addEventListener("change", saveSettings);
  $("#speaker-ip").addEventListener("change", () => { saveSettings(); checkStatus(); });
  $("#btn-copy-log").addEventListener("click", copyLog);
  $("#btn-clear-log").addEventListener("click", () => { $("#log").textContent = ""; });

  [
    "hm-url", "hm-host", "hm-port", "hm-user", "hm-pass", "hm-data", "hm-node",
    "hm-sudo-pass", "xp-host", "xp-port", "xp-user", "xp-pass", "xp-archive", "xp-wake-word",
  ].forEach((id) => {
    const node = document.getElementById(id);
    if (node) node.addEventListener("input", saveSettings);
  });

  $("#btn-download-patched").addEventListener("click", () => start("download", { model: S.model, kind: "patched" }));
  $("#btn-download-official").addEventListener("click", () => start("download", { model: S.model, kind: "official" }));
  $("#btn-patch").addEventListener("click", startPatchFlow);
  $("#btn-find-exe").addEventListener("click", () => start("find_exe", {}));
  $("#btn-flash").addEventListener("click", startFlash);
  $("#btn-restore").addEventListener("click", startRestore);
  $("#btn-identify").addEventListener("click", () => start("identify", { exe: $("#exe-path").value.trim() }));
  $("#btn-driver").addEventListener("click", () => start("install_driver", { exe: $("#exe-path").value.trim() }));
  $("#btn-tutorial").addEventListener("click", () => window.open(S.data.tutorial_url, "_blank"));

  $$("#deploy-tabs .tab").forEach((button) => {
    button.addEventListener("click", () => switchDeployTab(button.dataset.tab));
  });
  $$("#config-tabs .tab").forEach((button) => {
    button.addEventListener("click", () => switchConfigTab(button.dataset.ctab));
  });
  $("#btn-hm-connect").addEventListener("click", connectHomeMate);
  $("#btn-hm-open").addEventListener("click", () => {
    const url = normalizeUrl($("#hm-url").value);
    if (url) window.open(url, "_blank");
  });
  $("#btn-hm-deploy").addEventListener("click", deployHomeMate);
  $("#btn-hm-status").addEventListener("click", statusHomeMate);
  const openHmPage = (path) => {
    const url = normalizeUrl($("#hm-url").value);
    if (!url) return toast("请先在配置页填写 Home Mate 管理界面地址", true);
    window.open(`${url}${path}`, "_blank");
  };
  $("#btn-hm-services").addEventListener("click", () => openHmPage("/services"));
  $("#btn-hm-interaction").addEventListener("click", () => openHmPage("/interaction"));
  $("#btn-hm-devices").addEventListener("click", () => openHmPage("/devices"));
  $("#hm-sudo").addEventListener("change", () => {
    $("#hm-sudo-row").classList.toggle("hidden", !$("#hm-sudo").checked);
    saveSettings();
  });
  $("#btn-xp-status").addEventListener("click", () => xpAction("xp_status"));
  $("#btn-xp-install").addEventListener("click", async () => {
    const ok = await askConfirm(
      "将下载并安装最新 XiaoAI-Plus 到音箱 /data/xiaoai-plus（会覆盖同名程序文件）。继续吗？",
      { okText: "开始安装" },
    );
    if (!ok) return;
    if (!(await confirmUnsupported("xp"))) return;
    xpAction("xp_install");
  });
  $("#btn-xp-autostart").addEventListener("click", async () => {
    const ok = await askConfirm(
      "将把 boot.sh 写入音箱 /data/init.sh（旧文件备份为 init.sh.bak），开机自动启动 XiaoAI-Plus。继续吗？",
      { okText: "写入自启" },
    );
    if (!ok) return;
    if (!(await confirmUnsupported("xp"))) return;
    xpAction("xp_autostart");
  });
  $("#btn-xp-offline").addEventListener("click", async () => {
    const ssh = sshParams("xp");
    if (!ssh.host) return toast("请填写音箱地址", true);
    if (!(await confirmUnsupported("xp"))) return;
    start("xp_offline", { ssh, archive: $("#xp-archive").value.trim() });
  });
  $("#btn-xp-save").addEventListener("click", saveXpConfig);
  $("#btn-xp-start").addEventListener("click", () => xpAction("xp_start"));
  $("#btn-xp-stop").addEventListener("click", () => xpAction("xp_stop"));
  $("#btn-xp-restart").addEventListener("click", () => xpAction("xp_restart"));
  $("#btn-xp-logs").addEventListener("click", () => xpAction("xp_logs", { lines: 120 }));
  $("#btn-check").addEventListener("click", checkStatus);

  $("#btn-env-refresh").addEventListener("click", loadEnv);
  $("#btn-env-install").addEventListener("click", installDeps);
  $("#btn-env-next").addEventListener("click", () => {
    if (!S.ready) {
      toast("请先安装依赖", true);
      return;
    }
    switchView("flash");
  });
  $("#btn-setup-tools").addEventListener("click", () => api("/api/open-tools", { method: "POST", body: {} }));
  $("#btn-setup-tool-refresh").addEventListener("click", loadEnv);
  $("#btn-setup-tool-download").addEventListener("click", () => window.open(S.data.tool_url, "_blank"));

  $("#ssh-auth").addEventListener("change", toggleSshAuth);
  $("#ssh-cancel").addEventListener("click", closeSshModal);
  $("#btn-pick-key").addEventListener("click", async () => {
    const result = await api("/api/pick", { method: "POST", body: { kind: "key" } });
    if (result.path) $("#ssh-key").value = result.path;
  });
  $("#ssh-confirm").addEventListener("click", confirmRemoteDeploy);
}

function switchView(view) {
  if (view !== "setup" && !S.ready) {
    toast("请先完成初始化", true);
    view = "setup";
  }
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#view-flash").classList.toggle("hidden", view !== "flash");
  $("#view-deploy").classList.toggle("hidden", view !== "deploy");
  $("#view-config").classList.toggle("hidden", view !== "config");
  $("#view-setup").classList.toggle("hidden", view !== "setup");
  if (view === "setup") loadEnv();
}

function applyLock() {
  $$(".nav-item").forEach((button) => {
    const locked = button.dataset.view !== "setup" && !S.ready;
    button.classList.toggle("locked", locked);
    button.setAttribute("aria-disabled", locked ? "true" : "false");
  });
  const next = $("#btn-env-next");
  if (next) next.disabled = !S.ready;
}

async function loadEnv() {
  try {
    const env = await api("/api/env");
    S.env = env;
    renderEnv(env);
  } catch (error) {
    $("#env-hint").textContent = `检测失败：${error.message}`;
  }
}

function renderEnv(env) {
  const list = $("#env-list");
  list.innerHTML = "";
  const rows = [
    {
      name: "Python",
      ok: true,
      value: `${env.python.version}${env.python.venv ? "（.venv）" : ""} · ${env.python.path}`,
    },
    { name: "pywebview", ok: env.pywebview, value: env.pywebview ? "已安装" : "未安装" },
    { name: "paramiko", ok: env.paramiko, value: env.paramiko ? "已安装" : "未安装" },
  ];
  rows.forEach((row) => {
    const item = el("div", "env-item");
    item.appendChild(el("span", `dot ${row.ok ? "ok" : "err"}`));
    item.appendChild(el("span", "env-name", row.name));
    item.appendChild(el("span", "env-value", row.value));
    list.appendChild(item);
  });

  const missing = !env.pywebview || !env.paramiko;
  S.ready = !missing;
  applyLock();
  $("#btn-env-install").disabled = !missing;

  if (missing) {
    $("#env-hint").textContent = "缺少依赖。安装完成并重启控制台后，才能进入下一步。";
  } else if (!(env.tool && env.tool.ok)) {
    $("#env-hint").textContent = "依赖已就绪；刷机工具未找到，刷机前请先下载并放进 tools\\。";
  } else {
    $("#env-hint").textContent = "初始化完成，可以进入刷机或部署。";
  }

  const tool = env.tool || {};
  $("#setup-tool").textContent = tool.ok
    ? `已找到：${tool.path}`
    : "未找到。把 Amlogic 刷机工具 zip 放进 tools\\ 目录，或点「下载工具」。";
}

async function installDeps() {
  const button = $("#btn-env-install");
  button.disabled = true;
  button.textContent = "安装中…";
  $("#env-hint").textContent = "正在安装 pywebview、paramiko，请稍候…";
  try {
    const result = await api("/api/install-deps", { method: "POST", body: {} });
    if (result.env) renderEnv(result.env);
    if (result.ok) {
      log("依赖安装完成，重启控制台后生效。", "ok");
      $("#env-hint").textContent = "依赖安装完成，重启控制台后生效。";
    } else {
      log(result.output || "依赖安装失败。", "error");
      $("#env-hint").textContent = "依赖安装失败，详情见下方日志。";
    }
  } catch (error) {
    log(`依赖安装失败：${error.message}`, "error");
    $("#env-hint").textContent = `依赖安装失败：${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "安装依赖";
  }
}

async function pickFile(kind) {
  const result = await api("/api/pick", { method: "POST", body: { kind } });
  if (!result.path) return;
  if (kind === "image") {
    $("#image-path").value = result.path;
    updateImageInfo();
  } else if (kind === "archive") {
    $("#xp-archive").value = result.path;
  } else {
    $("#exe-path").value = result.path;
    $("#exe-info").className = "hint ok";
    $("#exe-info").textContent = `当前工具：${result.path}`;
  }
  saveSettings();
}

/* ---------------- 动作 ---------------- */

async function start(action, params) {
  if (S.busy) return;
  setBusy(true);
  S.lastAction = action;
  setStatus("正在执行…");
  try {
    const result = await api("/api/start", { method: "POST", body: { action, ...params } });
    if (!result.ok) {
      setBusy(false);
      setStatus("准备就绪");
      toast(result.message || "无法开始", true);
    }
  } catch (error) {
    setBusy(false);
    setStatus("准备就绪");
    toast(error.message, true);
  }
}

async function startFlash() {
  const exe = $("#exe-path").value.trim();
  const image = $("#image-path").value.trim();
  const model = currentModel();
  if (!exe) return toast("请先指定 Amlogic 刷机工具的 update.exe", true);
  if (!image) return toast("请先选择补丁固件", true);
  const ok = await askConfirm(
    `开始前请再确认：\n\n• 音箱是「${model.name}」\n• 当前固件是 ${model.version}\n• ${model.connection}\n\n` +
    `点「确定」后请立刻拔掉电源再插上。刷机有变砖风险，确定继续吗？`,
    { title: "确认开始刷机", okText: "开始刷机" },
  );
  if (!ok) return;
  start("flash", { exe, image, model: S.model });
}

async function startRestore() {
  const exe = $("#exe-path").value.trim();
  if (!exe) return toast("请先指定 update.exe", true);
  const ok = await askConfirm(
    "将把启动分区切回 boot1（原厂系统）。点「确定」后请立刻拔掉电源再插上。",
    { title: "恢复原厂系统", okText: "恢复" },
  );
  if (!ok) return;
  start("restore", { exe, model: S.model });
}

async function startPatchFlow() {
  const image = $("#image-path").value.trim();
  if (!image) return toast("请先选择官方固件（.squashfs）", true);

  let docker = { ok: false, message: "" };
  try {
    docker = await api("/api/docker");
  } catch (_) { /* ignore */ }

  if (docker.ok) {
    const ok = await askConfirm(
      "将用本机 Docker 把官方固件制作成补丁固件（开启 SSH、禁用自动更新等）。\n\n制作过程中请不要关闭控制台。",
      { title: "制作补丁固件", okText: "开始制作" },
    );
    if (ok) start("patch", { image, model: S.model, target: "local" });
    return;
  }

  const choice = await askChoice(
    "制作补丁固件",
    `本机没有可用的 Docker（${docker.message || "未检测到"}）。可以把官方固件上传到远程服务器，用那边的 Docker 制作。`,
    [
      { label: "使用远程服务器 Docker", value: "remote", primary: true },
      { label: "查看手动教程", value: "guide" },
      { label: "取消", value: null },
    ],
  );
  if (choice === "remote") openSshModal("patch");
  else if (choice === "guide") window.open(S.data.patch_guide_url, "_blank");
}

function openSshModal(purpose = "patch") {
  $("#ssh-confirm").textContent = "连接并制作";
  $("#ssh-hint").textContent = S.data.paramiko
    ? "需要服务器已安装 Docker，并可以用 SSH 登录。官方固件会临时上传到服务器。"
    : "本机缺少 paramiko，远程制作前需要安装（引导器会自动处理）。";

  const last = S.settings.last_ssh || {};
  if (last.host && !$("#ssh-host").value) $("#ssh-host").value = last.host;
  if (last.port && !$("#ssh-port").value) $("#ssh-port").value = last.port;
  if (last.user && !$("#ssh-user").value) $("#ssh-user").value = last.user;
  $("#ssh-modal").classList.remove("hidden");
}

function closeSshModal() {
  $("#ssh-modal").classList.add("hidden");
}

function toggleSshAuth() {
  const useKey = $("#ssh-auth").value === "key";
  $("#ssh-password-row").classList.toggle("hidden", useKey);
  $("#ssh-key-row").classList.toggle("hidden", !useKey);
}

function confirmRemoteDeploy() {
  const host = $("#ssh-host").value.trim();
  const user = $("#ssh-user").value.trim();
  const port = Number($("#ssh-port").value || 22);
  if (!host || !user) return toast("请填写主机地址和用户名", true);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return toast("SSH 端口不对", true);

  const useKey = $("#ssh-auth").value === "key";
  const ssh = { host, port, user, password: useKey ? "" : $("#ssh-password").value, key_path: useKey ? $("#ssh-key").value.trim() : "" };
  if (!useKey && !ssh.password) return toast("请填写 SSH 密码", true);
  if (useKey && !ssh.key_path) return toast("请选择 SSH 私钥", true);

  const image = $("#image-path").value.trim();
  if (!image) return toast("请先选择官方固件", true);
  S.ssh = ssh;
  S.settings.last_ssh = { host: ssh.host, port: ssh.port, user: ssh.user };
  closeSshModal();
  start("patch", { image, model: S.model, target: "remote", ssh });
}

/* ---------------- 状态 ---------------- */

function setDot(id, state) {
  const node = document.getElementById(id);
  node.className = "dot" + (state === true ? " ok" : state === false ? " err" : "");
}

function setOnlineText(id, text, state, emptyLabel = "未配置") {
  const node = document.getElementById(id);
  node.textContent = state === null ? emptyLabel : state ? "在线" : "离线";
  node.style.color = state === true ? "var(--ok)" : state === false ? "var(--err)" : "var(--muted)";
}

async function checkStatus() {
  try {
    const status = await api("/api/status");
    setDot("svc-dot", status.service);
    setOnlineText("svc-text", "服务端", status.service, "未部署");
    setDot("spk-dot", status.speaker);
    setOnlineText("spk-text", "音箱", status.speaker, "未配置");
    $("#svc-url").textContent = status.service_display || "";
  } catch (_) { /* 服务重启时忽略 */ }
}

/* ---------------- SSE ---------------- */

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch (_) { return; }
    if (event.type === "log") {
      log(event.text, event.level || "info");
    } else if (event.type === "status") {
      setStatus(event.text);
    } else if (event.type === "done") {
      handleDone(event);
    } else if (event.type === "error") {
      setBusy(false);
      setStatus("操作失败");
      log(event.text, "error");
      toast(event.text, true);
    }
  };
}

function handleDone(event) {
  setBusy(false);
  setStatus("准备就绪");
  const action = event.action;
  const result = event.result || "";

  if ((action === "download" || action === "patch") && result) {
    $("#image-path").value = result;
    updateImageInfo();
    saveSettings();
    toast(action === "patch" ? "补丁固件已生成" : "固件已下载");
  } else if (action === "find_exe" && result) {
    $("#exe-path").value = result;
    $("#exe-info").className = "hint ok";
    $("#exe-info").textContent = `已找到：${result}`;
    saveSettings();
  } else if (action === "identify") {
    toast(`设备识别成功：${result}`);
  } else if (action === "hm_deploy" || action === "hm_status") {
    const info = parseJson(result);
    if (action === "hm_deploy" && info.url) {
      $("#hm-url").value = info.url;
      connectHomeMate();
    }
    const state = info.state || (info.container ? "已创建" : "未知");
    $("#hm-deploy-hint").textContent =
      `容器：${state}｜控制台：${info.url || "-"}｜${info.reachable ? "控制台可访问" : "控制台暂不可访问（稍等再点连接）"}`;
  } else if (action === "xp_logs") {
    const info = parseJson(result);
    const text = (info.text || "").trim() || "（没有日志）";
    log(`XiaoAI-Plus 日志：\n${text}`);
    toast("日志已输出到下方操作控制台");
  } else if (
    ["xp_status", "xp_read", "xp_save", "xp_install", "xp_offline", "xp_start", "xp_stop", "xp_restart", "xp_autostart"].includes(action)
  ) {
    const info = parseJson(result);
    if (info.values) renderXpFields(info.values);
    if (info.wake_word) $("#xp-wake-word").value = info.wake_word;
    if (info.installed !== undefined || info.running !== undefined) applyXpStatus(info);
    const messages = {
      xp_status: "状态已刷新",
      xp_read: "已读取音箱配置",
      xp_save: "配置已保存并重启",
      xp_install: "安装完成",
      xp_offline: "离线安装完成",
      xp_start: "已启动",
      xp_stop: "已停止",
      xp_restart: "已重启",
      xp_autostart: "已设置开机自启",
    };
    toast(messages[action] || "完成");
  } else if (action === "install_driver") {
    toast("驱动安装程序已启动，请按提示完成安装");
  }
}

boot();
