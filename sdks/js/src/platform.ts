/** A streaming HTTP response, as a platform's stream opener returns it. */
export interface StreamResponse {
  status: number;
  header(name: string): string | null;
  body: AsyncIterable<Uint8Array>;
}

/** Opens the SSE stream: a GET of `url` with `headers`, cancelled by `signal`. */
export type StreamOpener = (
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
) => Promise<StreamResponse>;

/** A key-value store with the shape of `localStorage`; it may also be asynchronous. */
export interface StorageLike {
  getItem(key: string): string | null | Promise<string | null>;
  setItem(key: string, value: string): void | Promise<void>;
  removeItem(key: string): void | Promise<void>;
}

export type BackgroundState = "visible" | "hidden" | "terminated";

/** Tells the SDK when the app goes to the background or is about to end. */
export interface BackgroundSignal {
  current(): BackgroundState;
  subscribe(listener: (state: BackgroundState) => void): () => void;
}

/** What the SDK needs from the platform it runs on. Internal: used by the React Native package. */
export interface Platform {
  openStream: StreamOpener;
  storage: StorageLike | null;
  background: BackgroundSignal | null;
}
