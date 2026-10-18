/** An unfinished send keeps its original payload until the server confirms done. */
export interface PendingChat {
  id: string;
  message: string;
  conversationId: string | null;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function pendingKey(user: { tenant_id: string; user_id: string }): string {
  return `pending-chat:v1:${encodeURIComponent(user.tenant_id)}:${encodeURIComponent(user.user_id)}`;
}

export function newPendingChat(message: string, conversationId: string | null): PendingChat {
  return { id: crypto.randomUUID(), message, conversationId };
}

export function readPending(storage: StorageLike, key: string): PendingChat | null {
  try {
    const value = JSON.parse(storage.getItem(key) || "null");
    return value && typeof value.id === "string" && value.id.length > 0 && value.id.length <= 128
      && typeof value.message === "string" && value.message.trim()
      && (value.conversationId === null || typeof value.conversationId === "string")
      ? { id: value.id, message: value.message, conversationId: value.conversationId } : null;
  } catch { return null; }
}

export function savePending(storage: StorageLike, key: string, request: PendingChat | null): void {
  if (request) storage.setItem(key, JSON.stringify(request));
  else storage.removeItem(key);
}

export function abandonPending(storage: StorageLike, key: string): void {
  storage.removeItem(key);
}
