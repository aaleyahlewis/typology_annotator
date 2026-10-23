export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("We couldn't reach the study server. Check your connection and try again.", 0, {});
  }
  if (res.status === 401 && !path.endsWith("/login")) {
    location.href = "/";
    throw new ApiError("Signed out", 401, {});
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.detail || `Request failed (${res.status})`, res.status, data);
  return data;
}
