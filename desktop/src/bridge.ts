import { invoke, isTauri } from "@tauri-apps/api/core";

const hasHttpBridge =
  typeof window !== "undefined" &&
  !isTauri() &&
  Boolean(window.location?.protocol?.startsWith("http"));

export const connected = isTauri() || hasHttpBridge;

export async function request<T = any>(
  action: string,
  payload: object = {},
): Promise<T> {
  if (isTauri()) {
    return invoke<T>("desktop_request", { action, payload });
  }
  if (hasHttpBridge) {
    const resp = await fetch("/api/desktop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, payload }),
    });
    const body = await resp.json();
    if (!resp.ok || body.error) {
      throw new Error(body.error || `HTTP ${resp.status}`);
    }
    return body.result as T;
  }
  throw new Error(
    "未连接桌面服务，请通过 start-agent-helm.bat 或 start-agent-helm.ps1 启动。",
  );
}
