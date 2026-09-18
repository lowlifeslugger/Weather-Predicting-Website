// Shared helpers -- same-origin API, so no base URL needed.

function getToken() {
  return localStorage.getItem("sn_token");
}

function setToken(token) {
  localStorage.setItem("sn_token", token);
}

function clearToken() {
  localStorage.removeItem("sn_token");
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Wraps fetch: attaches the bearer token, and bounces to the login page
// on a 401 instead of leaving the UI stuck showing stale data.
async function apiFetch(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {}, authHeaders());
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    clearToken();
    window.location.href = "/";
    throw new Error("unauthenticated");
  }
  return res;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
