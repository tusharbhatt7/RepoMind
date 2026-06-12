// Anonymous workspace identity — no login.
//
// On first visit we mint a UUID and persist it under `repomind:tenant-id:v1`.
// Every API call sends it as `X-Tenant-Id`, which the backend uses to scope
// ChromaDB collections, agent logs, and metrics. Clearing localStorage or
// switching browsers means losing access to your data — that's the trade-off
// for skipping a login flow. Settings exposes Reset / Export / Import so a
// user can carry their tenant ID between devices manually.

const TENANT_KEY = "repomind:tenant-id:v1";

// Safe character class — matches the backend regex in auth.py (_TENANT_RE).
const TENANT_RE = /^[A-Za-z0-9._-]{1,128}$/;

function newUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (old Safari, jsdom).
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function isValidTenantId(id: string): boolean {
  return TENANT_RE.test(id) && !id.includes("__");
}

export function getOrCreateTenantId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = window.localStorage.getItem(TENANT_KEY) ?? "";
    if (!id || !isValidTenantId(id)) {
      id = newUuid();
      window.localStorage.setItem(TENANT_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

export function getTenantId(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TENANT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setTenantId(id: string): boolean {
  if (typeof window === "undefined") return false;
  const trimmed = id.trim();
  if (!isValidTenantId(trimmed)) return false;
  try {
    window.localStorage.setItem(TENANT_KEY, trimmed);
    return true;
  } catch {
    return false;
  }
}

export function resetTenantId(): string {
  const id = newUuid();
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(TENANT_KEY, id);
    } catch {
      /* private mode / quota — silent */
    }
  }
  return id;
}
