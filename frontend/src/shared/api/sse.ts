import { getFreshToken } from '@/shared/auth/keycloak';
import { env } from '@/shared/config/env';

/**
 * SSE-клиент `GET /api/v1/events/stream` (api-contract.md §2.2, OVERVIEW.md Р-10).
 *
 * Реализован на fetch + ReadableStream, а не на EventSource: браузерный
 * EventSource не умеет заголовок Authorization, а класть JWT в query нельзя.
 * Реконнект — экспоненциальный backoff 1с → 30с с джиттером, Last-Event-ID
 * передаётся при переподключении. Формат кадра: `event: <topic>\ndata: <json>`.
 */

export interface StreamEvent {
  /** Топик события: workflow.changed, request.transitioned, request.comment_added, telegram.linked, flags.updated, notification.created. */
  topic: string;
  data: unknown;
  id: string | null;
}

export type StreamListener = (event: StreamEvent) => void;
export type ConnectionListener = (connected: boolean) => void;

const WILDCARD = '*';
const MAX_BACKOFF_MS = 30_000;

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function done(): void {
      signal?.removeEventListener('abort', done);
      clearTimeout(timer);
      resolve();
    }
    signal?.addEventListener('abort', done, { once: true });
  });
}

class SseClient {
  private listeners = new Map<string, Set<StreamListener>>();
  private connectionListeners = new Set<ConnectionListener>();
  private abortController: AbortController | null = null;
  private lastEventId: string | null = null;
  private attempt = 0;
  private running = false;

  /** Открыть поток (идемпотентно). Вызывается после аутентификации. */
  connect(): void {
    if (this.running) return;
    this.running = true;
    this.attempt = 0;
    void this.loop();
  }

  close(): void {
    this.running = false;
    this.abortController?.abort();
    this.abortController = null;
    this.notifyConnection(false);
  }

  /** Подписка на топик (`'*'` — на все события). Возвращает функцию отписки. */
  subscribe(topic: string, listener: StreamListener): () => void {
    const set = this.listeners.get(topic) ?? new Set<StreamListener>();
    set.add(listener);
    this.listeners.set(topic, set);
    return () => {
      set.delete(listener);
    };
  }

  /** Подписка на состояние соединения — для баннера «Нет связи» (ux.md §4.1). */
  onConnectionChange(listener: ConnectionListener): () => void {
    this.connectionListeners.add(listener);
    return () => {
      this.connectionListeners.delete(listener);
    };
  }

  private notifyConnection(connected: boolean): void {
    for (const listener of this.connectionListeners) listener(connected);
  }

  private emit(event: StreamEvent): void {
    if (event.id) this.lastEventId = event.id;
    for (const listener of this.listeners.get(event.topic) ?? []) listener(event);
    for (const listener of this.listeners.get(WILDCARD) ?? []) listener(event);
  }

  private async loop(): Promise<void> {
    while (this.running) {
      this.abortController = new AbortController();
      const { signal } = this.abortController;
      try {
        const token = await getFreshToken();
        if (!token) throw new Error('no token');
        const headers: Record<string, string> = {
          Accept: 'text/event-stream',
          Authorization: `Bearer ${token}`,
        };
        if (this.lastEventId) headers['Last-Event-ID'] = this.lastEventId;
        const response = await fetch(`${env.apiUrl}/events/stream`, {
          headers,
          signal,
          cache: 'no-store',
        });
        if (!response.ok || !response.body) {
          throw new Error(`SSE HTTP ${response.status}`);
        }
        this.attempt = 0;
        this.notifyConnection(true);
        await this.readStream(response.body, signal);
      } catch {
        // разрыв или ошибка подключения — уходим в backoff ниже
      }
      this.notifyConnection(false);
      if (!this.running) break;
      this.attempt += 1;
      const backoff = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(this.attempt - 1, 5));
      await sleep(backoff + Math.random() * 500, signal);
    }
  }

  private async readStream(body: ReadableStream<Uint8Array>, signal: AbortSignal): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done || signal.aborted) break;
        buffer += decoder.decode(value, { stream: true });
        let frameEnd = buffer.search(/\r?\n\r?\n/);
        while (frameEnd !== -1) {
          const frame = buffer.slice(0, frameEnd);
          buffer = buffer.slice(frameEnd).replace(/^\r?\n\r?\n/, '');
          this.parseFrame(frame);
          frameEnd = buffer.search(/\r?\n\r?\n/);
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  private parseFrame(frame: string): void {
    let topic = 'message';
    let id: string | null = null;
    const dataLines: string[] = [];
    for (const rawLine of frame.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(':')) continue;
      const sep = rawLine.indexOf(':');
      const field = sep === -1 ? rawLine : rawLine.slice(0, sep);
      let value = sep === -1 ? '' : rawLine.slice(sep + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') topic = value;
      else if (field === 'data') dataLines.push(value);
      else if (field === 'id') id = value;
    }
    if (dataLines.length === 0) return;
    const raw = dataLines.join('\n');
    let data: unknown = raw;
    try {
      data = JSON.parse(raw) as unknown;
    } catch {
      // события с не-JSON data отдаём строкой
    }
    this.emit({ topic, data, id });
  }
}

/** Единственный экземпляр на приложение: подключает AuthProvider после логина. */
export const sseClient = new SseClient();
