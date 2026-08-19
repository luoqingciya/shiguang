// 拾光集插件 WebUI 桥：实现 AstrBotPluginPage 兼容接口
// Qingci 插件 Web API 前缀 /api/plugin-web/shiguang/，鉴权走 X-API-Key（与主 WebUI 同源共享 localStorage）
window.AstrBotPluginPage = (() => {
  const BASE = "/api/plugin-web/shiguang";

  function apiKey() {
    return localStorage.getItem("qingci_api_key") || "";
  }

  function authHeaders(extra = {}) {
    const headers = { ...extra };
    const key = apiKey();
    if (key) headers["X-API-Key"] = key;
    return headers;
  }

  async function parseResponse(res) {
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) message = body.error;
      } catch {
        /* keep HTTP status message */
      }
      throw new Error(message);
    }
    return res.json();
  }

  async function apiGet(endpoint, params = {}) {
    const query = new URLSearchParams(params).toString();
    const res = await fetch(`${BASE}/${endpoint}${query ? `?${query}` : ""}`, {
      headers: authHeaders(),
    });
    return parseResponse(res);
  }

  async function apiPost(endpoint, body = {}) {
    const res = await fetch(`${BASE}/${endpoint}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    return parseResponse(res);
  }

  async function upload(endpoint, file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/${endpoint}`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    });
    return parseResponse(res);
  }

  async function download(endpoint, params = {}, filename = "download.json") {
    const query = new URLSearchParams(params).toString();
    const res = await fetch(`${BASE}/${endpoint}${query ? `?${query}` : ""}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) message = body.error;
      } catch {
        /* keep HTTP status message */
      }
      throw new Error(message);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function ready() {
    /* Qingci 页面桥无需额外初始化 */
  }

  return { ready, apiGet, apiPost, download, upload };
})();
