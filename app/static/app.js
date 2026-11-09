"use strict";

const boot = window.AWG_BOOT || {};
const state = {
  dict:{}, lang:localStorage.getItem("awg-lang") || boot.lang || "ru", theme:localStorage.getItem("awg-theme") || boot.theme || "dark",
  csrf:"", servers:[], serverId:localStorage.getItem("awg-server-id") || "", dashboard:null, currentClient:null, currentConfig:null, filter:"all", busy:false, updatedAt:null, refreshTimer:null,
  sort:{field:localStorage.getItem("awg-sort-field") || "name", direction:localStorage.getItem("awg-sort-direction") || "asc"},
  defaults:{keepalive:30,dns:"",allowed_ips:"0.0.0.0/0,::/0"}
};
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];

function t(key, vars={}) { let value=state.dict[key] || key; Object.entries(vars).forEach(([name,replacement]) => value=value.replaceAll(`{${name}}`, replacement ?? "")); return value; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char])); }
function highlightSearch(value, query) {
  const text=String(value ?? "");
  if (!query) return escapeHtml(text);
  const pattern=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"),"giu");
  let result="",lastIndex=0;
  for (const match of text.matchAll(pattern)) {
    result+=escapeHtml(text.slice(lastIndex,match.index));
    result+=`<mark class="search-highlight">${escapeHtml(match[0])}</mark>`;
    lastIndex=match.index+match[0].length;
  }
  return result+escapeHtml(text.slice(lastIndex));
}
function localeDate(value, options={}) { if (!value) return ""; const date=new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(t("format.locale"), options).format(date); }
function normalizeKeepalive(value) { return Number.parseInt(String(value || "0"),10) || 0; }
function handshakeSeconds(value) { const match=String(value || "").match(/(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?/); return match ? (((+match[1]||0)*604800)+((+match[2]||0)*86400)+((+match[3]||0)*3600)+((+match[4]||0)*60)+(+match[5]||0)) : 0; }
function formatHandshake(value) { return value && value !== "0s" ? value : t("common.never"); }

function applyTranslations() {
  document.documentElement.lang=state.lang;
  $$('[data-i18n]').forEach(el => el.textContent=t(el.dataset.i18n));
  $$('[data-i18n-placeholder]').forEach(el => el.placeholder=t(el.dataset.i18nPlaceholder));
  $$('[data-i18n-title]').forEach(el => el.title=t(el.dataset.i18nTitle));
  if (!boot.login && state.dashboard) renderDashboard();
}
async function loadLocales() { const list=await fetch("/api/i18n").then(r=>r.json()); const select=$("#languageSelect"); select.innerHTML=list.locales.map(item=>`<option value="${escapeHtml(item.code)}">${escapeHtml(item.native_name)}</option>`).join(""); if (!list.locales.some(item=>item.code===state.lang)) state.lang=list.default; select.value=state.lang; state.dict=await fetch(`/api/i18n/${encodeURIComponent(state.lang)}`).then(r=>r.json()); applyTranslations(); }
async function changeLanguage(value) { state.lang=value; localStorage.setItem("awg-lang",value); document.cookie=`awg_admin_lang=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=strict`; state.dict=await fetch(`/api/i18n/${encodeURIComponent(value)}`).then(r=>r.json()); applyTranslations(); }
function setTheme(theme) { state.theme=theme; localStorage.setItem("awg-theme",theme); document.documentElement.dataset.theme=theme; }

function showToast(message) { const toast=$("#toast"); toast.textContent=message; toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer=setTimeout(()=>toast.classList.remove("show"),2600); }
async function api(path, options={}) { const headers={Accept:"application/json",...(options.headers||{})}; if (state.serverId) headers["X-AWG-Server"]=state.serverId; if (options.body && typeof options.body!=="string") { headers["Content-Type"]="application/json"; options.body=JSON.stringify(options.body); } if (options.method && options.method!=="GET" && state.csrf) headers["X-CSRF-Token"]=state.csrf; let response; try { response=await fetch(path,{...options,headers}); } catch(error) { throw {code:"NETWORK_ERROR",message_key:"error.network",technical:String(error)}; } if (response.status===401) { location.href="/login"; throw {code:"AUTH_REQUIRED"}; } let body={}; try { body=await response.json(); } catch(_) {} if (!response.ok) throw body.error || {code:`HTTP_${response.status}`,message_key:"error.unknown",technical:body.detail||response.statusText}; return body; }
function showError(error, inline=false) { const key=error?.message_key||"error.unknown"; let message=t(key)===key?t("error.unknown"):t(key); if (error?.hint_key && t(error.hint_key)!==error.hint_key) message+=` ${t(error.hint_key)}`; if (inline) { $("#runtimeErrorText").textContent=message; $("#runtimeError").classList.remove("hidden"); setConnection(false); return; } $("#errorMessage").textContent=message; $("#errorTechnical").textContent=error?.technical||error?.code||t("error.noTechnicalDetails"); $("#errorRequestId").textContent=error?.request_id?t("error.requestId",{id:error.request_id}):""; openModal($("#errorDialog")); }
function setConnection(online) { const el=$("#connectionStatus"); el.classList.toggle("online",online); el.classList.toggle("offline",!online); $("span",el).textContent=t(online?"status.connected":"status.disconnected"); }

function setBusy(busy, button=null) { state.busy=busy; $$('button,select,input').forEach(el => { if (busy) { el.dataset.disabledBeforeBusy=el.disabled?"1":"0"; el.disabled=true; } else { el.disabled=el.dataset.disabledBeforeBusy==="1"; delete el.dataset.disabledBeforeBusy; } }); if (button) button.classList.toggle("busy",busy); document.body.classList.toggle("app-busy",busy); }
async function withBusy(button, operation) { if (state.busy) return; setBusy(true,button); try { return await operation(); } finally { setBusy(false,button); } }
function openModal(dialog) { $$('dialog[open]').forEach(open => { if (open!==dialog) open.close(); }); if (!dialog.open) dialog.showModal(); }

function renderServerSelect() { const select=$("#serverSelect"); select.innerHTML=state.servers.map(server=>`<option value="${escapeHtml(server.id)}">${server.available?"":"⚠ "}${escapeHtml(server.name)} (${escapeHtml(server.mikrotik_host)})</option>`).join(""); select.value=state.serverId; }
function setSelectedServerAvailability(available) { const server=state.servers.find(item=>item.id===state.serverId); if (server) server.available=available; renderServerSelect(); }
async function loadServers() { const data=await api("/api/servers"); state.servers=data.servers||[]; if (!state.servers.some(item=>item.id===state.serverId)) state.serverId=data.default || state.servers[0]?.id || ""; localStorage.setItem("awg-server-id",state.serverId); renderServerSelect(); }
function clearDashboard() { state.dashboard=null; state.currentClient=null; state.currentConfig=null; state.updatedAt=null; $("#totalClients").textContent="—"; $("#activeClients").textContent="—"; $("#disabledClients").textContent="—"; $("#clientsBody").innerHTML=""; $("#emptyState").classList.add("hidden"); $("#syncWarning").classList.add("hidden"); $("#serverState").textContent="—"; $("#serverState").className=""; renderUpdatedAt(); }
async function changeServer(serverId) { state.serverId=serverId; localStorage.setItem("awg-server-id",serverId); clearDashboard(); $("#runtimeError").classList.add("hidden"); await loadDashboard(); }

function renderDashboard() {
  const data=state.dashboard, clients=data.clients||[], active=clients.filter(item=>!item.disabled).length;
  $("#totalClients").textContent=clients.length; $("#activeClients").textContent=active; $("#disabledClients").textContent=clients.length-active;
  state.defaults=data.server.client_defaults||state.defaults;
  const serverRunning=String(data.server.container_status||"").toLowerCase()==="running";
  $("#serverState").textContent=t(serverRunning?"server.running":"server.stopped"); $("#serverState").className=serverRunning?"server-running":"server-stopped";
  $("#syncWarning").classList.toggle("hidden",data.sync.ok); renderClients(); fillPools(); renderUpdatedAt();
}
function sortValue(client, field) { if (field==="disabled") return client.disabled?1:0; if (field==="rx") return Number(client.rx||0)+Number(client.tx||0); if (field==="last_handshake") return handshakeSeconds(client.last_handshake); return String(client[field]||"").toLocaleLowerCase(state.lang); }
function renderClients() {
  const all=state.dashboard?.clients||[], query=$("#clientSearch").value.trim(), normalizedQuery=query.toLocaleLowerCase(state.lang);
  $("#clearSearchButton").classList.toggle("hidden",!$("#clientSearch").value);
  let clients=all.filter(item => state.filter==="all" || (state.filter==="disabled" ? item.disabled : !item.disabled));
  clients=clients.filter(item => [item.name,item.comment,item.address].join(" ").toLocaleLowerCase(state.lang).includes(normalizedQuery));
  clients.sort((a,b) => { const av=sortValue(a,state.sort.field),bv=sortValue(b,state.sort.field); const result=typeof av==="number"?av-bv:String(av).localeCompare(String(bv),state.lang,{numeric:true,sensitivity:"base"}); return state.sort.direction==="asc"?result:-result; });
  $("#clientsBody").innerHTML=clients.map(client=>`<tr data-id="${escapeHtml(client.id)}"><td><div class="client-name"><strong>${highlightSearch(client.name,query)}</strong>${client.comment?`<small>${highlightSearch(client.comment,query)}</small>`:""}</div></td><td>${highlightSearch(client.address||"—",query)}</td><td><div class="status-cell"><button class="row-button toggle-button" data-action="toggle" title="${t(client.disabled?"clients.enable":"clients.disable")}">${client.disabled?"▶":"Ⅱ"}</button><div><span class="status-pill ${client.disabled?"disabled":""}">${t(client.disabled?"clients.disabled":"clients.enabled")}</span>${client.disabled_at?`<small class="disabled-at">${escapeHtml(localeDate(client.disabled_at,{dateStyle:"short",timeStyle:"short"}))}</small>`:""}</div></div></td><td>${escapeHtml(formatHandshake(client.last_handshake))}</td><td><div class="traffic"><span class="rx">${escapeHtml(client.rx_human)}</span><span class="tx">${escapeHtml(client.tx_human)}</span></div></td><td><div class="row-actions"><button class="row-button" data-action="config" title="${t("clients.showConfig")}">▦</button><button class="row-button" data-action="edit" title="${t("common.edit")}">✎</button><span class="action-separator"></span><button class="row-button delete-button" data-action="delete" title="${t("common.delete")}">♲</button></div></td></tr>`).join("");
  const empty=clients.length===0; $("#emptyState").classList.toggle("hidden",!empty); $("#emptyTitle").textContent=t(all.length?"clients.noMatches":"clients.empty"); $("#emptyHint").textContent=t(all.length?"clients.noMatchesHint":"clients.emptyHint");
  $$('.summary-card').forEach(card=>card.classList.toggle("active",card.dataset.filter===state.filter));
  $$('.sortable').forEach(th=>{ th.classList.toggle("active",th.dataset.sort===state.sort.field); const icon=$("i",th); icon.textContent=th.dataset.sort===state.sort.field?(state.sort.direction==="asc"?"↑":"↓"):"↕"; });
}
function fillPools() { const pools=state.dashboard?.pools||[]; $("#poolSelect").innerHTML=pools.map(pool=>`<option value="${escapeHtml(pool.cidr)}" ${pool.available?"":"disabled"}>${escapeHtml(pool.cidr)} — ${pool.available?t("clientForm.nextAddress",{address:pool.free_address}):t("clientForm.poolFull")}</option>`).join(""); }
function renderUpdatedAt() { $("#updatedAt").textContent=state.updatedAt?t("common.updatedAt",{date:localeDate(state.updatedAt,{dateStyle:"short",timeStyle:"medium"})}):""; }
async function loadDashboard() { try { state.dashboard=await api("/api/dashboard"); state.updatedAt=new Date(); $("#runtimeError").classList.add("hidden"); setSelectedServerAvailability(true); setConnection(true); renderDashboard(); return true; } catch(error) { setSelectedServerAvailability(false); showError(error,true); return false; } }

function setAddressModeOptions(editing) { const select=$("#addressMode"); select.innerHTML=(editing?["keep","next","manual"]:["next","manual"]).map(value=>`<option value="${value}">${t(`clientForm.addressMode.${value}`)}</option>`).join(""); updateAddressFields(); }
function updateAddressFields() { const mode=$("#addressMode").value; $("#poolField").classList.toggle("hidden",mode==="keep"); $("#manualAddressField").classList.toggle("hidden",mode!=="manual"); }
function updateKeyFields() { const form=$("#clientForm"); const store=form.elements.store_private_key.checked,usePsk=form.elements.use_preshared_key.checked; form.elements.private_key.disabled=!store; $("#generatePsk").disabled=!usePsk; form.elements.preshared_key.disabled=!usePsk; }
async function generateKeys(target="all") { const data=await api("/api/client-keys"),form=$("#clientForm"); if (target!=="psk") { form.elements.private_key.value=data.private_key; form.elements.public_key.value=data.public_key; } if (target!=="pair") form.elements.preshared_key.value=data.preshared_key; }
async function openClientDialog(client=null) {
  if (!await loadDashboard()) return; state.currentClient=client; const form=$("#clientForm"); form.reset(); setAddressModeOptions(!!client);
  form.elements.keepalive.value=client?normalizeKeepalive(client.keepalive):state.defaults.keepalive; form.elements.allowed_ips.value=client?.client_allowed_ips||state.defaults.allowed_ips; form.elements.name.value=client?.name||""; form.elements.comment.value=client?.comment||""; form.elements.dns.value=client?.dns||state.defaults.dns;
  if (client) { const details=await api(`/api/clients/${encodeURIComponent(client.id)}`); state.currentClient=details; form.elements.public_key.value=details.public_key||""; form.elements.private_key.value=details.private_key||""; form.elements.store_private_key.checked=details.store_private_key; form.elements.preshared_key.value=details.preshared_key||""; form.elements.use_preshared_key.checked=details.use_preshared_key; }
  else await generateKeys("all");
  updateKeyFields(); $("#clientDialogTitle").textContent=t(client?"clientForm.editTitle":"clientForm.addTitle"); openModal($("#clientDialog"));
}
async function saveClient(event) { event.preventDefault(); const form=event.currentTarget; const body={name:form.elements.name.value,comment:form.elements.comment.value,address_mode:form.elements.address_mode.value,pool:form.elements.pool.value,address:form.elements.address.value,dns:form.elements.dns.value,allowed_ips:form.elements.allowed_ips.value,keepalive:Number(form.elements.keepalive.value),public_key:form.elements.public_key.value,store_private_key:form.elements.store_private_key.checked,private_key:form.elements.private_key.value,use_preshared_key:form.elements.use_preshared_key.checked,preshared_key:form.elements.preshared_key.value}; const editing=!!state.currentClient; await withBusy($("#clientSubmit"),async()=>{ try { const result=await api(editing?`/api/clients/${encodeURIComponent(state.currentClient.id)}`:"/api/clients",{method:editing?"PUT":"POST",body}); $("#clientDialog").close(); showToast(t(editing?"toast.clientSaved":"toast.clientCreated")); await loadDashboard(); if (!editing&&result.config) openConfig(result); } catch(error) { showError(error); } }); }

async function showClientConfig(client) { try { openConfig(await api(`/api/clients/${encodeURIComponent(client.id)}/config`)); } catch(error) { showError(error); } }
function openConfig(data) { state.currentConfig=data; $("#configTitle").textContent=data.name; $("#configQr").src=data.qr; $("#configText").value=data.config; $("#vpnQr").src=data.vpn_qr; $("#vpnText").value=data.vpn_uri; $("#configWarning").classList.toggle("hidden",!data.private_key_missing); switchTab("awg"); openModal($("#configDialog")); }
function switchTab(name) { $$('[data-tab]').forEach(el=>el.classList.toggle("active",el.dataset.tab===name)); $$('[data-tab-content]').forEach(el=>el.classList.toggle("active",el.dataset.tabContent===name)); }
function confirmAction(title,message,danger=true) { return new Promise(resolve=>{ const dialog=$("#confirmDialog"); $("#confirmTitle").textContent=title; $("#confirmMessage").textContent=message; $("#confirmAction").classList.toggle("danger",danger); $("#confirmAction").classList.toggle("primary",!danger); dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true}); openModal(dialog); }); }
async function toggleClient(client,button) { const disabled=!client.disabled; if (!await confirmAction(t(disabled?"confirm.disableTitle":"confirm.enableTitle"),t(disabled?"confirm.disableMessage":"confirm.enableMessage",{name:client.name}),disabled)) return; await withBusy(button,async()=>{ await api(`/api/clients/${encodeURIComponent(client.id)}/disabled`,{method:"POST",body:{disabled}}); showToast(t(disabled?"toast.clientDisabled":"toast.clientEnabled")); await loadDashboard(); }); }
async function deleteClient(client,button) { if (!await confirmAction(t("confirm.deleteTitle"),t("confirm.deleteMessage",{name:client.name}))) return; await withBusy(button,async()=>{ await api(`/api/clients/${encodeURIComponent(client.id)}`,{method:"DELETE"}); showToast(t("toast.clientDeleted")); await loadDashboard(); }); }
async function synchronize(button) { let preview; await withBusy(button,async()=>{preview=await api("/api/sync");}); if (!preview||!await confirmAction(t("sync.confirmTitle"),t("sync.confirmMessage",{count:preview.target_keys.length}),false)) return; await withBusy(button,async()=>{ await api("/api/sync",{method:"POST",body:{confirm:true}}); showToast(t("toast.synchronized")); await loadDashboard(); }); }
async function openServerSettings() {
  const data=await api("/api/server/settings"); let section="";
  $("#settingsList").innerHTML=data.settings.map(item=>{
    const sectionWarning=item.section==="environment"?`<small class="settings-section-warning">${escapeHtml(t("serverSettings.environmentWarning"))}</small>`:"";
    const heading=item.section!==section?`<div class="settings-section"><strong>${escapeHtml(t(`serverSettings.section.${item.section}`))}</strong>${sectionWarning}</div>`:"";
    section=item.section;
    const description=item.description_key?`<small>${escapeHtml(t(item.description_key))}</small>`:"";
    const managed=item.managed?`<small class="managed-badge">${escapeHtml(t("serverSettings.managed"))}</small>`:"";
    const value=item.value_key?t(item.value_key):(item.value||"—");
    return `${heading}<div class="setting-row"><span class="setting-row-label"><span>${escapeHtml(item.label_key?t(item.label_key):item.key)}</span>${description}${managed}</span><span class="setting-value">${escapeHtml(value)}</span></div>`;
  }).join("");
  openModal($("#serverDialog"));
}
async function copyText(value) { if (navigator.clipboard&&isSecureContext) await navigator.clipboard.writeText(value); else { const area=document.createElement("textarea"); area.value=value; area.style.cssText="position:fixed;opacity:0;pointer-events:none"; document.body.appendChild(area); area.select(); if (!document.execCommand("copy")) throw new Error("clipboard copy failed"); area.remove(); } showToast(t("toast.copied")); }
function downloadConfig() { const blob=new Blob([state.currentConfig.config],{type:"text/plain;charset=utf-8"}),link=document.createElement("a"); link.href=URL.createObjectURL(blob); link.download=`${String(state.currentConfig.name||"client").replace(/[^a-z0-9_-]+/gi,"_")}.conf`; link.click(); URL.revokeObjectURL(link.href); }
function setAutoRefresh(value) { clearInterval(state.refreshTimer); localStorage.setItem("awg-auto-refresh",value); if (+value) state.refreshTimer=setInterval(()=>{ if (!state.busy&&!document.querySelector("dialog[open]")) loadDashboard(); },+value); }
function bindCommon() { setTheme(state.theme); $("#themeToggle")?.addEventListener("click",()=>setTheme(state.theme==="dark"?"light":"dark")); $("#languageSelect")?.addEventListener("change",event=>changeLanguage(event.target.value)); $$('[data-close]').forEach(button=>button.addEventListener("click",()=>{if(!state.busy)button.closest("dialog").close();})); $$('dialog').forEach(dialog=>{ dialog.addEventListener("click",event=>{if(event.target===dialog&&!state.busy)dialog.close();}); dialog.addEventListener("cancel",event=>{if(state.busy)event.preventDefault();}); }); }

async function startApp() {
  await loadLocales(); bindCommon(); if (boot.login) return; try { const session=await api("/api/session"); state.csrf=session.csrf; $("#authWarning").classList.toggle("hidden",!session.auth_disabled); $("#logoutButton").classList.toggle("hidden",session.auth_disabled); await loadServers(); } catch(error) { showError(error,true); return; }
  $("#serverSelect").addEventListener("change",event=>changeServer(event.target.value).catch(error=>showError(error,true)));
  $("#refreshButton").addEventListener("click",event=>withBusy(event.currentTarget,loadDashboard)); $("#retryButton").addEventListener("click",loadDashboard); $("#clientSearch").addEventListener("input",renderClients); $("#clearSearchButton").addEventListener("click",()=>{ $("#clientSearch").value=""; $("#clientSearch").focus(); renderClients(); });
  $$('.summary-card').forEach(card=>card.addEventListener("click",()=>{state.filter=card.dataset.filter;renderClients();})); $$('.sortable').forEach(th=>th.addEventListener("click",()=>{ if (state.sort.field===th.dataset.sort) state.sort.direction=state.sort.direction==="asc"?"desc":"asc"; else {state.sort.field=th.dataset.sort;state.sort.direction="asc";} localStorage.setItem("awg-sort-field",state.sort.field);localStorage.setItem("awg-sort-direction",state.sort.direction);renderClients();}));
  $("#addClientButton").addEventListener("click",event=>withBusy(event.currentTarget,()=>openClientDialog())); $("#clientForm").addEventListener("submit",saveClient); $("#addressMode").addEventListener("change",updateAddressFields); $("#clientForm").elements.store_private_key.addEventListener("change",updateKeyFields); $("#clientForm").elements.use_preshared_key.addEventListener("change",updateKeyFields); $("#generateKeyPair").addEventListener("click",event=>withBusy(event.currentTarget,()=>generateKeys("pair"))); $("#generatePsk").addEventListener("click",event=>withBusy(event.currentTarget,()=>generateKeys("psk")));
  $("#serverSettingsButton").addEventListener("click",event=>withBusy(event.currentTarget,openServerSettings)); $("#syncButton").addEventListener("click",event=>synchronize(event.currentTarget).catch(showError));
  $("#clientsBody").addEventListener("click",event=>{ const button=event.target.closest("button"),action=button?.dataset.action,id=event.target.closest("tr")?.dataset.id,client=state.dashboard?.clients.find(item=>item.id===id); if (!client||!action) return; if (action==="toggle") toggleClient(client,button).catch(showError); else if (action==="delete") deleteClient(client,button).catch(showError); else withBusy(button,()=>({config:showClientConfig,edit:openClientDialog}[action](client))).catch(showError); });
  $$('[data-tab]').forEach(button=>button.addEventListener("click",()=>switchTab(button.dataset.tab))); $("#copyConfig").addEventListener("click",()=>copyText(state.currentConfig.config).catch(showError)); $("#copyVpn").addEventListener("click",()=>copyText(state.currentConfig.vpn_uri).catch(showError)); $("#downloadConfig").addEventListener("click",downloadConfig);
  const refresh=localStorage.getItem("awg-auto-refresh")||"0"; $("#autoRefresh").value=refresh; $("#autoRefresh").addEventListener("change",event=>setAutoRefresh(event.target.value)); setAutoRefresh(refresh); await loadDashboard();
}
startApp().catch(error=>{if(!boot.login)showError(error,true);});
