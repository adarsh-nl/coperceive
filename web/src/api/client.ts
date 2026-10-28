import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type Job = {
  id: number;
  name: string;
  argv: string;
  status: string;
  output: string;
  created_us: number;
};

export function apiBase(): string {
  if (typeof sessionStorage !== "undefined") {
    const origin = sessionStorage.getItem("coperceive-origin");
    if (origin && !origin.startsWith("tauri://")) {
      return origin.replace(/\/$/, "");
    }
  }

  const base = new URL(".", document.baseURI).href.replace(/\/$/, "");
  if (base.startsWith("tauri://")) {
    if (typeof sessionStorage !== "undefined") {
      const origin = sessionStorage.getItem("coperceive-origin");
      if (origin && !origin.startsWith("tauri://")) {
        return origin.replace(/\/$/, "");
      }
    }
  }
  return base;
}

export function authHeaders(token: string): Record<string, string> {
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

export function clientFor(token: string) {
  return createClient<paths>({ baseUrl: apiBase(), headers: authHeaders(token) });
}

export function isJob(value: unknown): value is Job {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Record<string, unknown>;
  return (
    typeof row.id === "number" &&
    typeof row.name === "string" &&
    typeof row.argv === "string" &&
    typeof row.status === "string" &&
    typeof row.output === "string" &&
    typeof row.created_us === "number"
  );
}

export async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // The body was not JSON.
  }
  return `HTTP ${response.status}`;
}
