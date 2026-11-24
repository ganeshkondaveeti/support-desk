// Reviewer token for mutating routes (arch §12: ADMIN_TOKEN bearer). It lives in
// sessionStorage for this tab only. No ticket or draft text is ever stored.
const KEY = "support-desk.reviewer-token";

type Listener = () => void;
const listeners = new Set<Listener>();
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string): void {
  memoryToken = token;
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // Storage blocked: keep it in memory until reload.
  }
  notify();
}

export function clearToken(): void {
  memoryToken = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing stored.
  }
  notify();
}

/** Set by the client on a 401 so the shell can ask for the token. */
let tokenRequested = false;
export function requestToken(): void {
  tokenRequested = true;
  notify();
}
export function isTokenRequested(): boolean {
  return tokenRequested;
}
export function dismissTokenRequest(): void {
  tokenRequested = false;
  notify();
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify(): void {
  for (const listener of listeners) listener();
}
