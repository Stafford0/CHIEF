const DEFAULT_TIMEOUT = 15_000;
const TOKEN_SESSION_KEY = "chief.api.token";
let bearerToken = "";
try {
  bearerToken = sessionStorage.getItem(TOKEN_SESSION_KEY) || "";
} catch {
  // Some hardened browser contexts disable storage; in-memory pairing still works.
}

export class ChiefApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ChiefApiError";
  }
}

export function hasChiefApiToken(): boolean {
  return Boolean(bearerToken);
}

export function setChiefApiToken(token: string): void {
  bearerToken = token.trim();
  try {
    if (bearerToken) sessionStorage.setItem(TOKEN_SESSION_KEY, bearerToken);
    else sessionStorage.removeItem(TOKEN_SESSION_KEY);
  } catch {
    // Keep the token in memory for this page when session storage is unavailable.
  }
}

export async function requestJson<T>(url: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT): Promise<T> {
  const target = new URL(url, window.location.href);
  const loopback = ["localhost", "127.0.0.1", "::1"].includes(target.hostname);
  if (bearerToken && target.protocol !== "https:" && !loopback) {
    throw new ChiefApiError(
      "CHIEF will not send a pairing token over an unencrypted remote connection",
      0,
    );
  }
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(init.signal?.reason);
  init.signal?.addEventListener("abort", forwardAbort, { once: true });
  const timer = window.setTimeout(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), timeoutMs);
  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...init.headers,
        ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {}),
      },
    });
    if (!response.ok) throw new ChiefApiError(`CHIEF request failed (${response.status})`, response.status);
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw new Error("CHIEF returned an unexpected response format");
    return await response.json() as T;
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener("abort", forwardAbort);
  }
}

export async function streamJsonLines<T>(
  url: string,
  init: RequestInit,
  onEvent: (event: T) => void,
): Promise<void> {
  const target = new URL(url, window.location.href);
  const loopback = ["localhost", "127.0.0.1", "::1"].includes(target.hostname);
  if (bearerToken && target.protocol !== "https:" && !loopback) {
    throw new ChiefApiError(
      "CHIEF will not send a pairing token over an unencrypted remote connection",
      0,
    );
  }
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/x-ndjson",
      ...init.headers,
      ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {}),
    },
  });
  if (!response.ok) throw new ChiefApiError(`CHIEF request failed (${response.status})`, response.status);
  if (!response.body) throw new Error("CHIEF returned an empty response stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  while (true) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value, { stream: !done });
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as T);
    }
    if (done) break;
  }
  if (buffered.trim()) onEvent(JSON.parse(buffered) as T);
}
