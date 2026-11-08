const AUTH_KEY = "rw_auth";

export function getAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function isLoggedIn() {
  const auth = getAuth();
  return !!(auth && auth.logged_in && auth.user_id);
}

export function setAuth(payload) {
  if (!payload?.user_id) return;
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({
      user_id: payload.user_id,
      username: payload.username || "",
      display_name: payload.display_name || payload.username || payload.user_id,
      logged_in: true,
    })
  );
  window.dispatchEvent(new CustomEvent("rw-auth-change"));
}

export function clearAuth() {
  localStorage.removeItem(AUTH_KEY);
  window.dispatchEvent(new CustomEvent("rw-auth-change"));
}

export function getUserId() {
  if (isLoggedIn()) return getAuth().user_id;
  return "default";
}

export function authHeaders(extra = {}) {
  return { ...extra, "X-User-Id": getUserId() };
}

export async function request(url, options = {}) {
  const headers = { ...(options.headers || {}), "X-User-Id": getUserId() };
  const response = await fetch(url, { ...options, headers });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail = (body && (body.detail || body.message)) || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

/** User-initiated pause / new request aborted the fetch — not a server failure. */
export function isAbortError(error) {
  if (!error) return false;
  if (error.name === "AbortError") return true;
  const msg = String(error.message || "").toLowerCase();
  return msg.includes("aborted") || msg.includes("abort");
}

export async function readStream(response, onEvent, { signal } = {}) {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      /* 保持状态码说明 */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((item) => item.startsWith("data: "));
        if (line) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* 单条 SSE 损坏时继续读后续过程事件 */
          }
        }
      }
    }
  } catch (error) {
    if (isAbortError(error) || signal?.aborted) return;
    throw error;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}
