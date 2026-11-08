"use strict";

const boot = window.AWG_BOOT || {};
const state = {dict:{}, lang:localStorage.getItem("awg-lang") || boot.lang || "ru", theme:localStorage.getItem("awg-theme") || boot.theme || "dark", csrf:"", dashboard:null, currentClient:null, defaults:{keepalive:30,dns:"",allowed_ips:"0.0.0.0/0,::/0"}};
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];

function t(key, vars={}) {
  let value = state.dict[key] || key;
  Object.entries(vars).forEach(([name,replacement]) => value = value.replaceAll(`{${name}}`, replacement ?? ""));
  return value;
}

function applyTranslations() {
  document.documentElement.lang = state.lang;
  $$('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));
  $$('[data-i18n-placeholder]').forEach(el => el.placeholder = t(el.dataset.i18nPlaceholder));
  if (!boot.login && state.dashboard) renderDashboard();
}

async function loadLocales() {
  const list = await fetch("/api/i18n").then(r => r.json());
  const select = $("#languageSelect");
  select.innerHTML = list.locales.map(item => `<option value="${escapeHtml(item.code)}">${escapeHtml(item.native_name)}</option>`).join("");
  if (!list.locales.some(item => item.code === state.lang)) state.lang = list.default;
  select.value = state.lang;
  state.dict = await fetch(`/api/i18n/${encodeURIComponent(state.lang)}`).then(r => r.json());
  applyTranslations();
}

async function changeLanguage(value) {
  state.lang = value;
  localStorage.setItem("awg-lang", value);
  document.cookie = `awg_admin_lang=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=strict`;
  state.dict = await fetch(`/api/i18n/${encodeURIComponent(value)}`).then(r => r.json());
  applyTranslations();
}

function setTheme(theme) {
  state.theme = theme;
  localStorage.setItem("awg-theme", theme);
  document.documentElement.dataset.theme = theme;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

async function api(path, options={}) {
  const headers = {"Accept":"application/json", ...(options.headers || {})};
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (options.method && options.method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
  let response;
  try { response = await fetch(path, {...options, headers}); }
  catch (error) { throw {code:"NETWORK_ERROR", message_key:"error.network", technical:String(error)}; }
  if (response.status === 401) { window.location.href = "/login"; throw {code:"AUTH_REQUIRED"}; }
  let body = {};
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok) {
    if (body.error) throw body.error;
    throw {code:`HTTP_${response.status}`, message_key:"error.unknown", technical:body.detail || response.statusText};
  }
  return body;
}

function showError(error, inline=false) {
  const key = error?.message_key || "error.unknown";
  let message = t(key) === key ? t("error.unknown") : t(key);
  if (error?.hint_key && t(error.hint_key) !== error.hint_key) message += ` ${t(error.hint_key)}`;
  if (inline) {
    $("#runtimeErrorText").textContent = message;
    $("#runtimeError").classList.remove("hidden");
    setConnection(false);
    return;
  }
  $("#errorMessage").textContent = message;
  $("#errorTechnical").textContent = error?.technical || error?.code || t("error.noTechnicalDetails");
  $("#errorRequestId").textContent = error?.request_id ? t("error.requestId", {id:error.request_id}) : "";
  $("#errorDialog").showModal();
}

function setConnection(online) {
  const el = $("#connectionStatus");
  el.classList.toggle("online", online);
  el.classList.toggle("offline", !online);
  $("span", el).textContent = t(online ? "status.connected" : "status.disconnected");
}

function normalizeKeepalive(value) { return Number.parseInt(String(value || "0"), 10) || 0; }
function formatHandshake(value) { return value && value !== "0s" ? value : t("common.never"); }

function renderDashboard() {
  const data = state.dashboard;
  const clients = data.clients || [];
  const active = clients.filter(item => !item.disabled).length;
  $("#totalClients").textContent = clients.length;
  $("#activeClients").textContent = active;
  $("#disabledClients").textContent = clients.length - active;
  $("#serverState").textContent = data.server.container_status || t("common.unknown");
  $("#serverMeta").textContent = `${data.server.wireguard_interface} · ${data.server.client_endpoint}`;
  $("#syncWarning").classList.toggle("hidden", data.sync.ok);
  renderClients();
  fillPools();
}

function renderClients() {
  const query = $("#clientSearch").value.trim().toLowerCase();
  const clients = (state.dashboard?.clients || []).filter(item => [item.name,item.comment,item.address,item.public_key,item.endpoint].join(" ").toLowerCase().includes(query));
  $("#clientsBody").innerHTML = clients.map(client => `<tr data-id="${escapeHtml(client.id)}">
    <td><div class="client-name"><strong>${escapeHtml(client.name)}</strong><small title="${escapeHtml(client.public_key)}">${escapeHtml(client.comment || client.public_key)}</small></div></td>
    <td>${escapeHtml(client.address || "—")}</td>
    <td><span class="status-pill ${client.disabled ? "disabled" : ""}">${t(client.disabled ? "clients.disabled" : "clients.enabled")}</span>${client.disabled_at ? `<div class="muted">${escapeHtml(client.disabled_at)}</div>` : ""}</td>
    <td>${escapeHtml(formatHandshake(client.last_handshake))}</td>
    <td><div class="traffic"><span class="rx">${escapeHtml(client.rx_human)}</span><span class="tx">${escapeHtml(client.tx_human)}</span></div></td>
    <td>${escapeHtml(client.endpoint || "—")}</td>
    <td><div class="row-actions">
      <button data-action="config" title="${t("clients.showConfig")}">▦</button><button data-action="edit" title="${t("common.edit")}">✎</button>
      <button data-action="toggle" title="${t(client.disabled ? "clients.enable" : "clients.disable")}">${client.disabled ? "▶" : "Ⅱ"}</button><button data-action="delete" title="${t("common.delete")}">×</button>
    </div></td></tr>`).join("");
  $("#emptyState").classList.toggle("hidden", clients.length > 0);
}

function fillPools() {
  const pools = state.dashboard?.pools || [];
  $("#poolSelect").innerHTML = pools.map(pool => `<option value="${escapeHtml(pool.cidr)}" ${pool.available ? "" : "disabled"}>${escapeHtml(pool.cidr)} — ${pool.available ? t("clientForm.nextAddress", {address:pool.free_address}) : t("clientForm.poolFull")}</option>`).join("");
}

async function loadDashboard() {
  $("#refreshButton").disabled = true;
  try {
    state.dashboard = await api("/api/dashboard");
    $("#runtimeError").classList.add("hidden");
    setConnection(true);
    renderDashboard();
  } catch (error) { showError(error, true); }
  finally { $("#refreshButton").disabled = false; }
}

function openClientDialog(client=null) {
  state.currentClient = client;
  const form = $("#clientForm");
  form.reset();
  form.elements.keepalive.value = client ? normalizeKeepalive(client.keepalive) : state.defaults.keepalive;
  form.elements.allowed_ips.value = client?.client_allowed_ips || state.defaults.allowed_ips;
  form.elements.name.value = client?.name || "";
  form.elements.comment.value = client?.comment || "";
  form.elements.dns.value = client?.dns || state.defaults.dns;
  $$(".create-only", form).forEach(el => el.classList.toggle("hidden", !!client));
  $$(".edit-only", form).forEach(el => el.classList.toggle("hidden", !client));
  $("#clientDialogTitle").textContent = t(client ? "clientForm.editTitle" : "clientForm.addTitle");
  $("#clientDialog").showModal();
}

async function saveClient(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = {name:form.elements.name.value,comment:form.elements.comment.value,dns:form.elements.dns.value,allowed_ips:form.elements.allowed_ips.value,keepalive:Number(form.elements.keepalive.value)};
  if (state.currentClient) body.psk_action = form.elements.psk_action.value;
  else { body.pool = form.elements.pool.value; body.use_preshared_key = form.elements.use_preshared_key.checked; }
  $("#clientSubmit").disabled = true;
  try {
    const result = await api(state.currentClient ? `/api/clients/${encodeURIComponent(state.currentClient.id)}` : "/api/clients", {method:state.currentClient ? "PUT" : "POST", body});
    $("#clientDialog").close();
    showToast(t(state.currentClient ? "toast.clientSaved" : "toast.clientCreated"));
    await loadDashboard();
    if (!state.currentClient && result.config) openConfig(result);
  } catch (error) { showError(error); }
  finally { $("#clientSubmit").disabled = false; }
}

async function showClientConfig(client) {
  try { openConfig(await api(`/api/clients/${encodeURIComponent(client.id)}/config`)); }
  catch (error) { showError(error); }
}

function openConfig(data) {
  state.currentConfig = data;
  $("#configTitle").textContent = data.name;
  $("#configQr").src = data.qr;
  $("#configText").value = data.config;
  $("#vpnQr").src = data.vpn_qr;
  $("#vpnText").value = data.vpn_uri;
  switchTab("awg");
  $("#configDialog").showModal();
}

function switchTab(name) {
  $$('[data-tab]').forEach(el => el.classList.toggle("active", el.dataset.tab === name));
  $$('[data-tab-content]').forEach(el => el.classList.toggle("active", el.dataset.tabContent === name));
}

function confirmAction(title, message, danger=true) {
  return new Promise(resolve => {
    const dialog = $("#confirmDialog");
    $("#confirmTitle").textContent = title;
    $("#confirmMessage").textContent = message;
    $("#confirmAction").classList.toggle("danger", danger);
    $("#confirmAction").classList.toggle("primary", !danger);
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once:true});
    dialog.showModal();
  });
}

async function toggleClient(client) {
  const disabled = !client.disabled;
  if (!await confirmAction(t(disabled ? "confirm.disableTitle" : "confirm.enableTitle"), t(disabled ? "confirm.disableMessage" : "confirm.enableMessage", {name:client.name}), disabled)) return;
  try { await api(`/api/clients/${encodeURIComponent(client.id)}/disabled`, {method:"POST",body:{disabled}}); showToast(t(disabled ? "toast.clientDisabled" : "toast.clientEnabled")); await loadDashboard(); }
  catch (error) { showError(error); }
}

async function deleteClient(client) {
  if (!await confirmAction(t("confirm.deleteTitle"), t("confirm.deleteMessage", {name:client.name}))) return;
  try { await api(`/api/clients/${encodeURIComponent(client.id)}`, {method:"DELETE"}); showToast(t("toast.clientDeleted")); await loadDashboard(); }
  catch (error) { showError(error); }
}

async function synchronize() {
  const preview = await api("/api/sync").catch(error => {showError(error); return null;});
  if (!preview || !await confirmAction(t("sync.confirmTitle"), t("sync.confirmMessage", {count:preview.target_keys.length}), false)) return;
  try { await api("/api/sync", {method:"POST",body:{confirm:true}}); showToast(t("toast.synchronized")); await loadDashboard(); }
  catch (error) { showError(error); }
}

async function openServerSettings() {
  try {
    const data = await api("/api/server/settings");
    $("#settingsList").innerHTML = data.settings.map(item => `<div class="setting-row"><span>${escapeHtml(item.label_key ? t(item.label_key) : item.key)}</span><span class="setting-value">${escapeHtml(item.sensitive ? "••••••••" : item.value || "—")}</span></div>`).join("");
    $("#serverDialog").showModal();
  } catch (error) { showError(error); }
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
  else {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.cssText = "position:fixed;opacity:0;pointer-events:none";
    document.body.appendChild(area);
    area.select();
    if (!document.execCommand("copy")) throw new Error("clipboard copy failed");
    area.remove();
  }
  showToast(t("toast.copied"));
}

function downloadConfig() {
  const blob = new Blob([state.currentConfig.config], {type:"text/plain;charset=utf-8"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${String(state.currentConfig.name || "client").replace(/[^a-z0-9_-]+/gi,"_")}.conf`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function bindCommon() {
  setTheme(state.theme);
  $("#themeToggle")?.addEventListener("click", () => setTheme(state.theme === "dark" ? "light" : "dark"));
  $("#languageSelect")?.addEventListener("change", event => changeLanguage(event.target.value));
  $$('[data-close]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
}

async function startApp() {
  await loadLocales();
  bindCommon();
  if (boot.login) return;
  try {
    const session = await api("/api/session");
    state.csrf = session.csrf;
    state.defaults = session.client_defaults || state.defaults;
    $("#authWarning").classList.toggle("hidden", !session.auth_disabled);
    $("#logoutButton").classList.toggle("hidden", session.auth_disabled);
  } catch (_) { return; }
  $("#refreshButton").addEventListener("click", loadDashboard);
  $("#retryButton").addEventListener("click", loadDashboard);
  $("#clientSearch").addEventListener("input", renderClients);
  $("#addClientButton").addEventListener("click", () => openClientDialog());
  $("#clientForm").addEventListener("submit", saveClient);
  $("#serverSettingsButton").addEventListener("click", openServerSettings);
  $("#syncButton").addEventListener("click", synchronize);
  $("#clientsBody").addEventListener("click", event => {
    const action = event.target.closest("button")?.dataset.action;
    const id = event.target.closest("tr")?.dataset.id;
    const client = state.dashboard?.clients.find(item => item.id === id);
    if (!client || !action) return;
    ({config:showClientConfig,edit:openClientDialog,toggle:toggleClient,delete:deleteClient})[action](client);
  });
  $$('[data-tab]').forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  $("#copyConfig").addEventListener("click", () => copyText(state.currentConfig.config).catch(showError));
  $("#copyVpn").addEventListener("click", () => copyText(state.currentConfig.vpn_uri).catch(showError));
  $("#downloadConfig").addEventListener("click", downloadConfig);
  await loadDashboard();
}

startApp().catch(error => { if (!boot.login) showError(error, true); });
