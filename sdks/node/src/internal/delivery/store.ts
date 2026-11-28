// The current ruleset, filled from the bootstrap file, the disk cache, the
// SSE stream and the poller.

import type { Ruleset } from "../engine/model.js";

export type Source = "bootstrap" | "cache" | "stream" | "poll";

/** Snapshot is one parsed ruleset with the ETag and the source it came from. */
export interface Snapshot {
  readonly ruleset: Ruleset;
  readonly etag: string;
  readonly source: Source;
}

/**
 * Store holds the current snapshot. Node.js has one thread, so readers take
 * no lock and always see a whole snapshot.
 */
export class Store {
  private current: Snapshot | undefined;

  /** load returns the current snapshot, or undefined before the first swap. */
  load(): Snapshot | undefined {
    return this.current;
  }

  /** swap installs next and returns the snapshot it replaced. */
  swap(next: Snapshot): Snapshot | undefined {
    const old = this.current;
    this.current = next;
    return old;
  }
}
