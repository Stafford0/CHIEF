const DEFAULT_TIMEOUT = 15_000;

export async function requestJson<T>(url: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT): Promise<T> {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(init.signal?.reason);
  init.signal?.addEventListener("abort", forwardAbort, { once: true });
  const timer = window.setTimeout(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal, headers: { Accept: "application/json", ...init.headers } });
    if (!response.ok) throw new Error(`CHIEF request failed (${response.status})`);
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw new Error("CHIEF returned an unexpected response format");
    return await response.json() as T;
  } finally {
    window.clearTimeout(timer);
    init.signal?.removeEventListener("abort", forwardAbort);
  }
}
