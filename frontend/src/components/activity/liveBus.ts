/**
 * DEV-004 shared live-event bus (W3 publisher + W4 subscriber).
 *
 * CORRECTION NOTE (W3): this file was created independently by W4
 * (ActivityPanel imports `subscribeLiveBus` + a `LiveBusEvent` with
 * `id`/`event_type`/`conversationId`) while W3 ran in parallel and briefly
 * overwrote it with an incompatible shape. This merged version restores
 * W4's API exactly and keeps W3's `publish`/`subscribe` aliases stable —
 * both workers share this file, W4 owns rendering, W3 owns publishing.
 */

export interface LiveBusEvent {
  id: number;
  conversationId: string;
  turnId: string;
  event_type: string;
  label: string;
  detail: string;
  at: number;
}

type Listener = (e: LiveBusEvent) => void;

const listeners = new Set<Listener>();

export function publish(e: LiveBusEvent): void {
  for (const fn of Array.from(listeners)) {
    try {
      fn(e);
    } catch {
      // A single bad subscriber must never break the chat stream.
    }
  }
}

/** Alias kept for W4's import name. */
export const publishLiveBus = publish;

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Alias kept for W4's import name. */
export const subscribeLiveBus = subscribe;
