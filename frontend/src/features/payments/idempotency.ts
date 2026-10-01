interface PendingRequest { fingerprint: string; key: string }

/** Keep one key for one request payload, including retries after a tab refresh. */
export function pendingKey(scope: string, payload: unknown): string {
  const fingerprint = JSON.stringify(payload);
  const storageKey = `orderflow.pending.${scope}`;
  const existing = sessionStorage.getItem(storageKey);
  if (existing) {
    try {
      const request = JSON.parse(existing) as PendingRequest;
      if (request.fingerprint === fingerprint && request.key) return request.key;
    } catch { /* Replace a malformed session record. */ }
  }
  const key = crypto.randomUUID();
  sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key } satisfies PendingRequest));
  return key;
}

export function clearPendingKey(scope: string): void {
  sessionStorage.removeItem(`orderflow.pending.${scope}`);
}
