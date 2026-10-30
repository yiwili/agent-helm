import { invoke, isTauri } from "@tauri-apps/api/core";
export const connected = isTauri();
export async function request<T = any>(
  action: string,
  payload: object = {},
): Promise<T> {
  if (!connected)
    throw new Error(
      "浏览器预览未连接桌面服务，请通过 npm run dev 启动 Tauri。",
    );
  return invoke<T>("desktop_request", { action, payload });
}
