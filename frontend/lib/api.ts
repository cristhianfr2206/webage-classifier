export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function csrfToken(): string {
  const entry = document.cookie
    .split("; ")
    .find((cookie) => cookie.startsWith("csrf_token="));
  return entry ? decodeURIComponent(entry.split("=")[1]) : "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.method && init.method !== "GET"
        ? { "X-CSRF-Token": csrfToken() }
        : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}

export type BrowserInspection = {
  id: string;
  run_id: string;
  status: string;
  trigger: string;
  rendered_title: string;
  rendered_text_sample: string;
  failure_code: string | null;
  artifact_id: string | null;
};
