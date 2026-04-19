import { z } from "zod";

const envelopeSchema = <T extends z.ZodTypeAny>(schema: T) =>
  z.object({
    ok: z.literal(true),
    data: schema
  });

function formatHttpErrorDetail(json: unknown): string | undefined {
  if (!json || typeof json !== "object") return undefined;
  const rec = json as Record<string, unknown>;
  const err = rec.error;
  if (err && typeof err === "object" && err !== null) {
    const m = (err as { message?: unknown }).message;
    if (typeof m === "string" && m.trim()) return m;
  }
  const detail = rec.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const msg = (item as { msg?: unknown }).msg;
          const loc = (item as { loc?: unknown }).loc;
          const locStr = Array.isArray(loc) ? loc.join(".") : "";
          return locStr ? `${locStr}: ${String(msg)}` : String(msg);
        }
        try {
          return JSON.stringify(item);
        } catch {
          return String(item);
        }
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return undefined;
}

async function request<T extends z.ZodTypeAny>(path: string, schema: T, init?: RequestInit): Promise<z.infer<T>> {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json"
    },
    ...init
  });
  const text = await response.text();
  let json: unknown = null;

  if (text.trim()) {
    try {
      json = JSON.parse(text);
    } catch {
      if (!response.ok) {
        throw new Error(text.trim() || response.statusText || "Request failed");
      }
      throw new Error(text.trim() || "Server returned a non-JSON response.");
    }
  }

  if (!response.ok) {
    const message =
      formatHttpErrorDetail(json)
      || text.trim()
      || response.statusText
      || "Request failed";
    throw new Error(message);
  }

  if (!json) {
    throw new Error("Server returned an empty response.");
  }

  if ((json as { ok?: boolean }).ok === false) {
    const message = formatHttpErrorDetail(json) || "Request failed";
    throw new Error(message);
  }

  return envelopeSchema(schema).parse(json).data;
}

export function getJson<T extends z.ZodTypeAny>(path: string, schema: T) {
  return request(path, schema);
}

export function postJson<T extends z.ZodTypeAny>(path: string, schema: T, body?: unknown) {
  return request(path, schema, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined
  });
}

export function putJson<T extends z.ZodTypeAny>(path: string, schema: T, body?: unknown) {
  return request(path, schema, {
    method: "PUT",
    body: body !== undefined ? JSON.stringify(body) : undefined
  });
}

export function patchJson<T extends z.ZodTypeAny>(path: string, schema: T, body?: unknown) {
  return request(path, schema, {
    method: "PATCH",
    body: body ? JSON.stringify(body) : undefined
  });
}

export function deleteJson<T extends z.ZodTypeAny>(path: string, schema: T) {
  return request(path, schema, { method: "DELETE" });
}
