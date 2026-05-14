// ===================================================================
// MLLP Gateway — Dashboard
// ===================================================================

let allMessages = [];  // [{...msg, _kind, _msgType, _patient}, ...]
let currentFilter = "all";
let searchQuery = "";

// -------------------------------------------------------------------
// WebSocket — single connection for all data + real-time events
// -------------------------------------------------------------------

let ws = null;
let wsReconnectTimer = null;
let _reqId = 0;
const _pending = new Map();  // id -> {resolve, reject, timer}

function _nextId() { return ++_reqId; }

function wsRequest(type, extra = {}) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return reject(new Error("WebSocket not connected"));
    }
    const id = _nextId();
    const timer = setTimeout(() => {
      _pending.delete(id);
      reject(new Error("request timed out"));
    }, 30000);
    _pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ type, id, ...extra }));
  });
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    const el = document.getElementById("wsStatus");
    el.className = "ws-status live";
    document.getElementById("wsLabel").textContent = "Live";
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
    // Load initial data over the new connection
    loadInitial();
  };

  ws.onclose = () => {
    const el = document.getElementById("wsStatus");
    el.className = "ws-status offline";
    document.getElementById("wsLabel").textContent = "Reconnecting\u2026";
    // Reject any pending requests
    for (const [, p] of _pending) { clearTimeout(p.timer); p.reject(new Error("disconnected")); }
    _pending.clear();
    wsReconnectTimer = setTimeout(connectWS, 3000);
  };

  ws.onerror = () => { ws.close(); };

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);

    // Response to a request we sent
    if (msg.id != null && _pending.has(msg.id)) {
      const p = _pending.get(msg.id);
      _pending.delete(msg.id);
      clearTimeout(p.timer);
      p.resolve(msg);
      return;
    }

    // Server-push event
    const { event, data } = msg;
    if (event === "received_message" || event === "sent_message") {
      addMessage(data);
      refreshStats();
    } else if (event === "forward_status") {
      const idx = allMessages.findIndex(m => m.id === data.id);
      if (idx !== -1) {
        allMessages[idx].forwarded = data.forwarded ? 1 : 0;
      }
      const card = document.querySelector(`.msg-card[data-id="${data.id}"] .fwd-badge`);
      if (card) {
        card.className = data.forwarded ? "badge success fwd-badge" : "badge pending fwd-badge";
        card.textContent = data.forwarded ? "FORWARDED" : "PENDING";
      }
      refreshStats();
    }
  };
}

// -------------------------------------------------------------------
// HL7 quick info extraction
// -------------------------------------------------------------------

function extractHL7Info(raw) {
  if (!raw) return { msgType: "", patient: "" };
  const lines = raw.split(/\r?\n/).filter(l => l.trim());
  let msgType = "";
  let patient = "";

  for (const line of lines) {
    const f = line.split("|");
    if (f[0] === "MSH" && f.length > 8) {
      msgType = f[8] || "";
    }
    if (f[0] === "PID" && f.length > 5 && f[5]) {
      const parts = f[5].split("^");
      patient = parts.length >= 2 ? `${parts[0]}, ${parts[1]}` : parts[0];
    }
  }
  return { msgType, patient };
}

// -------------------------------------------------------------------
// Relative time
// -------------------------------------------------------------------

function relativeTime(iso) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 0)    return "just now";
  if (diff < 60)   return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

// -------------------------------------------------------------------
// Message management
// -------------------------------------------------------------------

function addMessage(m) {
  const info = extractHL7Info(m.message);
  m._msgType = info.msgType;
  m._patient = info.patient;
  allMessages.unshift(m);
  renderMessages();
}

function renderMessages() {
  const container = document.getElementById("messageList");
  const empty = document.getElementById("emptyMessages");

  // Filter
  let filtered = allMessages;
  if (currentFilter === "received") {
    filtered = allMessages.filter(m => m.kind === "received");
  } else if (currentFilter === "sent") {
    filtered = allMessages.filter(m => m.kind === "sent");
  }

  // Search
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    filtered = filtered.filter(m =>
      (m.message || "").toLowerCase().includes(q) ||
      (m._msgType || "").toLowerCase().includes(q) ||
      (m._patient || "").toLowerCase().includes(q) ||
      (m.peer || "").toLowerCase().includes(q) ||
      (m.host || "").toLowerCase().includes(q)
    );
  }

  if (filtered.length === 0) {
    container.innerHTML = "";
    if (empty) {
      empty.style.display = "";
      empty.textContent = allMessages.length === 0
        ? "No messages yet. Waiting for HL7 traffic\u2026"
        : "No messages match your filter.";
    }
    return;
  }

  if (empty) empty.style.display = "none";

  // Build HTML
  const html = filtered.map(m => {
    const isReceived = m.kind === "received";
    const iconClass = isReceived ? "received" : "sent";
    const iconLabel = isReceived ? "IN" : "OUT";
    const peer = isReceived
      ? (m.peer || "unknown")
      : (m.host ? `${m.host}:${m.port}` : (m.peer || ""));
    const time = relativeTime(m.time);
    const typeDisplay = m._msgType || m.kind.toUpperCase();

    let badges = "";
    if (isReceived) {
      badges += `<span class="badge received">RECEIVED</span>`;
      if (m.forwarded) {
        badges += `<span class="badge success fwd-badge">FORWARDED</span>`;
      } else {
        badges += `<span class="badge pending fwd-badge">PENDING</span>`;
      }
    } else {
      badges += `<span class="badge sent">SENT</span>`;
      if (m.status === "success") {
        badges += `<span class="badge success">${escapeHtml(m.status.toUpperCase())}</span>`;
      } else if (m.status) {
        badges += `<span class="badge error">${escapeHtml(m.status.toUpperCase())}</span>`;
      }
    }

    const patientLine = m._patient
      ? `<div class="msg-patient">${escapeHtml(m._patient)}</div>`
      : "";

    return `<a class="msg-card" href="/message/${m.id}" data-id="${m.id}" data-kind="${m.kind}">
      <div class="msg-icon ${iconClass}">${iconLabel}</div>
      <div class="msg-body">
        <div class="msg-top">
          <span class="msg-type">${escapeHtml(typeDisplay)}</span>
          <span class="msg-peer">${escapeHtml(peer)}</span>
        </div>
        ${patientLine}
      </div>
      <div class="msg-meta">
        <span class="msg-time">${time}</span>
        <div>${badges}</div>
      </div>
    </a>`;
  }).join("");

  container.innerHTML = html;
}

// -------------------------------------------------------------------
// Stats
// -------------------------------------------------------------------

async function refreshStats() {
  try {
    const resp = await wsRequest("get_stats");
    const s = resp.data;
    document.getElementById("statReceived").textContent = s.received;
    document.getElementById("statForwarded").textContent = s.forwarded;
    document.getElementById("statSent").textContent = s.sent;
    document.getElementById("statDevices").textContent = s.devices;
  } catch (_) {}
}

// -------------------------------------------------------------------
// Connections
// -------------------------------------------------------------------

async function refreshConnections() {
  try {
    const resp = await wsRequest("get_connections");
    const data = resp.data;
    _connData = data;  // cache for send form
    const container = document.getElementById("devices");
    const ips = Object.keys(data);

    if (ips.length === 0) {
      container.innerHTML = '<div class="empty-state">No devices connected</div>';
      return;
    }

    container.innerHTML = '<div class="device-grid">' + ips.map(ip => {
      const s = data[ip];
      const oruDot = s.oru_connected ? "on-oru" : "off";
      const ormDot = s.orm_connected ? "on-orm" : "off";
      return `<div class="device-chip">
        <span class="dot ${oruDot}"></span>
        <span class="dot ${ormDot}"></span>
        <span>${escapeHtml(ip)}</span>
      </div>`;
    }).join("") + '</div>';
  } catch (_) {}
}

// -------------------------------------------------------------------
// Tabs & search
// -------------------------------------------------------------------

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    currentFilter = tab.dataset.filter;
    renderMessages();
  });
});

document.getElementById("searchInput").addEventListener("input", (e) => {
  searchQuery = e.target.value;
  renderMessages();
});

// -------------------------------------------------------------------
// Send card (expandable) with mode-aware device selection
// -------------------------------------------------------------------

const sendPanel = document.getElementById("sendPanel");
const sendForm = document.getElementById("sendForm");
const sendError = document.getElementById("sendError");
const sendDevice = document.getElementById("sendDevice");
const sendMode = document.getElementById("sendMode");
const manualHostRow = document.getElementById("manualHostRow");
const modeHint = document.getElementById("modeHint");

let _connData = {};

const MODE_HINTS = {
  client: "Opens a new MLLP connection to the device\u2019s listener port. Requires host & port.",
  shared: "Sends on the existing ORU connection the device initiated. No port needed.",
  server: "Sends on the existing ORM connection the device initiated. No port needed.",
};

document.getElementById("sendToggle").addEventListener("click", () => {
  sendPanel.classList.toggle("open");
  if (sendPanel.classList.contains("open")) {
    _populateDeviceSelect();
    sendDevice.focus();
  }
});

function _populateDeviceSelect() {
  const current = sendDevice.value;
  sendDevice.innerHTML = '<option value="">\u2014 Manual IP \u2014</option>';
  for (const ip of Object.keys(_connData)) {
    const s = _connData[ip];
    const labels = [];
    if (s.oru_connected) labels.push("ORU");
    if (s.orm_connected) labels.push("ORM");
    const opt = document.createElement("option");
    opt.value = ip;
    opt.textContent = `${ip} (${labels.join("+")})`;
    sendDevice.appendChild(opt);
  }
  if (current && _connData[current]) sendDevice.value = current;
  _onDeviceChange();
}

function _onDeviceChange() {
  const ip = sendDevice.value;
  if (!ip) {
    manualHostRow.style.display = "";
    _setModeOptions(["client", "shared", "server"]);
    sendMode.value = "client";
  } else {
    const modes = (_connData[ip] && _connData[ip].send_modes) || ["client"];
    _setModeOptions(modes);
    if (modes.includes("shared")) sendMode.value = "shared";
    else if (modes.includes("server")) sendMode.value = "server";
    else sendMode.value = "client";
  }
  _onModeChange();
}

function _setModeOptions(modes) {
  for (const opt of sendMode.options) {
    opt.disabled = !modes.includes(opt.value);
  }
}

function _onModeChange() {
  const mode = sendMode.value;
  const ip = sendDevice.value;
  modeHint.textContent = MODE_HINTS[mode] || "";
  if (mode === "client" || !ip) {
    manualHostRow.style.display = "";
  } else {
    manualHostRow.style.display = "none";
  }
}

sendDevice.addEventListener("change", _onDeviceChange);
sendMode.addEventListener("change", _onModeChange);

sendForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const mode = sendMode.value;
  const deviceIp = sendDevice.value;
  const manualHost = document.getElementById("sendHost").value.trim();
  const port = parseInt(document.getElementById("sendPort").value, 10) || 2575;
  const message = document.getElementById("sendMessage").value.trim();
  const btn = document.getElementById("sendSubmit");
  const label = document.getElementById("sendBtnLabel");
  const spinner = document.getElementById("sendBtnSpinner");

  const host = deviceIp || manualHost;
  if (!host) {
    sendError.textContent = "Select a device or enter a host IP.";
    sendError.style.display = "";
    return;
  }

  sendError.style.display = "none";
  btn.disabled = true;
  label.textContent = "Sending\u2026";
  spinner.style.display = "";

  try {
    const resp = await wsRequest("send", { host, port, message, mode });
    if (resp.data.error) {
      sendError.textContent = resp.data.error;
      sendError.style.display = "";
    } else {
      document.getElementById("sendMessage").value = "";
      sendError.style.display = "none";
    }
  } catch (err) {
    sendError.textContent = "Error: " + err.message;
    sendError.style.display = "";
  } finally {
    btn.disabled = false;
    label.textContent = "Send Message";
    spinner.style.display = "none";
  }
});

// -------------------------------------------------------------------
// Initial load
// -------------------------------------------------------------------

async function loadInitial() {
  try {
    const resp = await wsRequest("get_messages");
    const { sent, received } = resp.data;

    const merged = [];
    for (const m of sent) {
      const info = extractHL7Info(m.message);
      m._msgType = info.msgType;
      m._patient = info.patient;
      merged.push(m);
    }
    for (const m of received) {
      const info = extractHL7Info(m.message);
      m._msgType = info.msgType;
      m._patient = info.patient;
      merged.push(m);
    }
    merged.sort((a, b) => b.id - a.id);
    allMessages = merged;
    renderMessages();
  } catch (_) {
    const empty = document.getElementById("emptyMessages");
    if (empty) empty.textContent = "Failed to load messages.";
  }

  refreshStats();
  refreshConnections();
}

// connectWS triggers loadInitial once connected
connectWS();

// Periodic refresh for connections and relative times
setInterval(refreshConnections, 10000);
setInterval(refreshStats, 30000);
setInterval(renderMessages, 60000);  // Update relative times
