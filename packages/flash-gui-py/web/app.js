/* Open-XiaoAI 控制台前端 */

const S = {
  data: null,
  settings: {},
  model: "LX06",
  mode: "xiaozhi",
  values: {},
  method: "image",
  busy: false,
  lastAction: "",
  ssh: null,
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

function setBusy(busy) {
  S.busy = busy;
  $("#busy").classList.toggle("hidden", !busy);
  const ids = [
    "btn-flash", "btn-restore", "btn-identify", "btn-download",
    "btn-deploy-local", "btn-deploy-remote", "btn-find-exe", "btn-driver",
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

/* ---------------- 设置 ---------------- */

let saveTimer = null;
function saveSettings() {
  S.settings.model = S.model;
  S.settings.mode = S.mode;
  S.settings.method = S.method;
  S.settings.memory = $("#memory").value.trim();
  S.settings.mirror = $("#mirror").value;
  S.settings.mirror_prefix = $("#mirror-prefix").value.trim();
  S.settings.image_prefix = $("#image-prefix").value.trim();
  S.settings.port = $("#port").value.trim();
  S.settings.image = $("#image-path").value;
  S.settings.exe = $("#exe-path").value;
  S.settings.speaker_ip = $("#speaker-ip").value.trim();
  S.settings.values = S.values;
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
    node.textContent = `还没有选择固件。点「下载官方固件」自动下载 ${model.filename}，或手动选择已下载的文件。`;
    return;
  }
  const name = path.split(/[\\/]/).pop();
  node.className = "hint ok";
  node.textContent = `${name} ｜ ${model.name} 的固件`;
}

/* ---------------- 部署模式 ---------------- */

function renderModes() {
  const box = $("#mode-list");
  box.innerHTML = "";
  S.data.modes.forEach((mode) => {
    const card = el("button", "mode" + (mode.key === S.mode ? " active" : ""));
    card.type = "button";
    card.appendChild(el("div", "mode-title", mode.title));
    card.appendChild(el("div", "mode-sub", mode.tagline));
    if (!mode.deployable) card.appendChild(el("span", "mode-tag", "源码运行"));
    card.addEventListener("click", () => selectMode(mode.key));
    box.appendChild(card);
  });
}

function currentMode() {
  return S.data.modes.find((mode) => mode.key === S.mode) || S.data.modes[0];
}

function ensureValues(mode) {
  if (!S.values[mode.key]) {
    const values = {};
    mode.fields.forEach((field) => { values[field.key] = field.default || ""; });
    S.values[mode.key] = values;
  }
  return S.values[mode.key];
}

function selectMode(key) {
  S.mode = key;
  const mode = currentMode();
  renderModes();
  $("#mode-title").textContent = mode.title;
  $("#mode-tagline").textContent = mode.tagline;
  $("#mode-doc").href = `${S.data.github_url}/blob/main/examples/${mode.key}/README.md`;
  const note = $("#mode-note");
  if (mode.note) {
    note.textContent = mode.note;
    note.classList.remove("hidden");
  } else {
    note.classList.add("hidden");
  }
  renderFields(mode);
  $("#btn-deploy-local").disabled = !mode.deployable;
  $("#btn-deploy-remote").disabled = !mode.deployable;
  saveSettings();
}

function renderFields(mode) {
  const box = $("#fields");
  box.innerHTML = "";
  const values = ensureValues(mode);
  mode.fields.forEach((field) => {
    const wrap = el("div", "field");
    const label = el("label", "", field.label + (field.required ? " *" : ""));
    let input;
    if (field.multiline) {
      input = el("textarea");
      input.value = values[field.key] ?? "";
    } else {
      input = el("input");
      if (field.secret) input.type = "password";
      input.value = values[field.key] ?? "";
      input.placeholder = field.placeholder || "";
    }
    input.addEventListener("input", () => {
      values[field.key] = input.value;
      saveSettings();
    });
    wrap.appendChild(label);
    wrap.appendChild(input);
    if (field.help) wrap.appendChild(el("div", "hint", field.help));
    box.appendChild(wrap);
  });
}

/* ---------------- 选项 ---------------- */

function renderOptions() {
  const methodBox = $("#method-seg");
  methodBox.innerHTML = "";
  [["image", "官方镜像（最快）"], ["source", "源码构建（ARM / 小内存）"]].forEach(([value, label]) => {
    const button = el("button", value === S.method ? "active" : "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      S.method = value;
      renderOptions();
      saveSettings();
    });
    methodBox.appendChild(button);
  });

  const mirror = $("#mirror");
  mirror.innerHTML = "";
  S.data.mirrors.forEach((item) => {
    const option = el("option", "", item.label);
    option.value = item.key;
    mirror.appendChild(option);
  });
  mirror.value = S.settings.mirror || "auto";
  mirror.addEventListener("change", saveSettings);
}

function applySettings() {
  const st = S.settings;
  $("#memory").value = st.memory || "";
  $("#mirror-prefix").value = st.mirror_prefix || "";
  $("#image-prefix").value = st.image_prefix || "";
  $("#port").value = st.port || S.data.defaults.port;
  $("#speaker-ip").value = st.speaker_ip || "";
  $("#image-path").value = st.image || "";
  $("#exe-path").value = st.exe || "";
  S.method = st.method || "image";
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
    S.mode = S.settings.mode || S.data.defaults.mode;
    if (!S.data.modes.some((mode) => mode.key === S.mode)) S.mode = S.data.defaults.mode;

    applySettings();
    renderModels();
    renderModes();
    renderOptions();
    selectMode(S.mode);

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
    if (!location.search.includes("no-sse")) connectEvents();
    checkStatus();
    setInterval(checkStatus, 15000);
    if (new URLSearchParams(location.search).get("view") === "deploy") switchView("deploy");
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
  $("#memory").addEventListener("input", saveSettings);
  $("#mirror-prefix").addEventListener("input", saveSettings);
  $("#image-prefix").addEventListener("input", saveSettings);
  $("#port").addEventListener("input", saveSettings);

  $("#btn-download").addEventListener("click", () => start("download", { model: S.model }));
  $("#btn-find-exe").addEventListener("click", () => start("find_exe", {}));
  $("#btn-flash").addEventListener("click", startFlash);
  $("#btn-restore").addEventListener("click", startRestore);
  $("#btn-identify").addEventListener("click", () => start("identify", { exe: $("#exe-path").value.trim() }));
  $("#btn-driver").addEventListener("click", () => start("install_driver", { exe: $("#exe-path").value.trim() }));
  $("#btn-tutorial").addEventListener("click", () => window.open(S.data.tutorial_url, "_blank"));

  $("#btn-deploy-local").addEventListener("click", deployLocal);
  $("#btn-deploy-remote").addEventListener("click", () => openSshModal());
  $("#btn-check").addEventListener("click", checkStatus);

  $("#ssh-auth").addEventListener("change", toggleSshAuth);
  $("#ssh-cancel").addEventListener("click", closeSshModal);
  $("#btn-pick-key").addEventListener("click", async () => {
    const result = await api("/api/pick", { method: "POST", body: { kind: "key" } });
    if (result.path) $("#ssh-key").value = result.path;
  });
  $("#ssh-confirm").addEventListener("click", confirmRemoteDeploy);
}

function switchView(view) {
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#view-flash").classList.toggle("hidden", view !== "flash");
  $("#view-deploy").classList.toggle("hidden", view !== "deploy");
}

async function pickFile(kind) {
  const result = await api("/api/pick", { method: "POST", body: { kind } });
  if (!result.path) return;
  if (kind === "image") {
    $("#image-path").value = result.path;
    updateImageInfo();
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

function startFlash() {
  const exe = $("#exe-path").value.trim();
  const image = $("#image-path").value.trim();
  const model = currentModel();
  if (!exe) return toast("请先指定 Amlogic 刷机工具的 update.exe", true);
  if (!image) return toast("请先选择补丁固件", true);
  const ok = window.confirm(
    `开始前请再确认：\n\n• 音箱是「${model.name}」\n• 当前固件是 ${model.version}\n• ${model.connection}\n\n` +
    `点「确定」后请立刻拔掉电源再插上。刷机有变砖风险，确定继续吗？`
  );
  if (!ok) return;
  start("flash", { exe, image, model: S.model });
}

function startRestore() {
  const exe = $("#exe-path").value.trim();
  if (!exe) return toast("请先指定 update.exe", true);
  if (!window.confirm("将把启动分区切回 boot1（原厂系统）。点「确定」后请立刻拔掉电源再插上。")) return;
  start("restore", { exe, model: S.model });
}

function deployParams(target, ssh = null) {
  const mode = currentMode();
  const port = Number($("#port").value || 4399);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    toast("端口需要是 1-65535 的数字", true);
    return null;
  }
  const memory = $("#memory").value.trim();
  if (memory && !/^\d+[mMgG]$/.test(memory)) {
    toast("内存上限格式不对，例如 512m、1g", true);
    return null;
  }
  const values = { ...ensureValues(mode) };
  const missing = mode.fields.filter((field) => field.required && !String(values[field.key] || "").trim());
  if (missing.length) {
    toast(`请填写：${missing.map((field) => field.label).join("、")}`, true);
    return null;
  }
  return {
    mode: mode.key,
    values,
    port,
    method: S.method,
    memory,
    mirror: $("#mirror").value,
    mirror_prefix: $("#mirror-prefix").value.trim(),
    image_prefix: $("#image-prefix").value.trim(),
    target,
    ssh,
  };
}

function deployLocal() {
  const mode = currentMode();
  if (!mode.deployable) return toast(mode.note || "该接入方式暂不支持一键部署", true);
  const params = deployParams("local");
  if (!params) return;
  const methodText = S.method === "source" ? "源码构建（本机下载并编译）" : `官方镜像 ${mode.image}`;
  if (!window.confirm(`将用 Docker 在本机启动 ${mode.title}：\n\n方式：${methodText}\n端口：${params.port}\n内存上限：${params.memory || "不限制"}\n\n继续吗？`)) return;
  start("deploy", params);
}

function openSshModal() {
  const mode = currentMode();
  if (!mode.deployable) return toast(mode.note || "该接入方式暂不支持一键部署", true);
  $("#ssh-modal").classList.remove("hidden");
  $("#ssh-hint").textContent = S.data.paramiko
    ? "需要服务器已安装 Docker，并且可以用 SSH 登录。"
    : "本机缺少 paramiko，远程部署前需要安装（引导器会自动处理）。";
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

  const params = deployParams("remote", ssh);
  if (!params) return;
  S.ssh = ssh;
  closeSshModal();
  start("deploy", params);
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

  if (action === "download" && result) {
    $("#image-path").value = result;
    updateImageInfo();
    saveSettings();
    toast("固件已下载并校验通过");
  } else if (action === "find_exe" && result) {
    $("#exe-path").value = result;
    $("#exe-info").className = "hint ok";
    $("#exe-info").textContent = `已找到：${result}`;
    saveSettings();
  } else if (action === "identify") {
    toast(`设备识别成功：${result}`);
  } else if (action === "deploy" && result) {
    toast(`部署完成：${result}`);
    checkStatus();
  } else if (action === "install_driver") {
    toast("驱动安装程序已启动，请按提示完成安装");
  }
}

boot();
