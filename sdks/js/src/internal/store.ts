// The evaluated results the provider reads from, and what changed between
// two of them. The store holds one StoredResults or none and swaps it in one
// assignment.
import type { EvaluateResult } from "./options.js";

/** Where the stored results came from. */
export type Source = "bootstrap" | "cache" | "network";

export interface StoredResults {
  results: ReadonlyMap<string, EvaluateResult>;
  etag: string | null;
  source: Source;
  contextHash: string;
  targetingKey: string | null;
}

export class Store {
  #current: StoredResults | undefined;

  get(): StoredResults | undefined {
    return this.#current;
  }

  set(s: StoredResults | undefined): void {
    this.#current = s;
  }
}

/** storedFrom keys results by flag key and adds the metadata. */
export function storedFrom(
  results: readonly EvaluateResult[],
  meta: Omit<StoredResults, "results">,
): StoredResults {
  return { ...meta, results: new Map(results.map((r) => [r.key, r])) };
}

/**
 * changedFlags lists, sorted, the keys whose value (JSON equality) or variant
 * differs between a and b, plus the keys present in only one of them.
 */
export function changedFlags(a: StoredResults, b: StoredResults): string[] {
  const changed: string[] = [];
  for (const [key, ra] of a.results) {
    const rb = b.results.get(key);
    if (!rb || ra.variant !== rb.variant || !sameJson(ra.value, rb.value)) {
      changed.push(key);
    }
  }
  for (const key of b.results.keys()) {
    if (!a.results.has(key)) changed.push(key);
  }
  return changed.sort();
}

/**
 * sameJson is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays
 * element by element, objects key by key, {} is not [].
 */
export function sameJson(a: unknown, b: unknown): boolean {
  if (a === null || b === null) return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((x, i) => sameJson(x, b[i]))
    );
  }
  if (typeof a === "object" && typeof b === "object") {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const keys = Object.keys(ao);
    return (
      keys.length === Object.keys(bo).length &&
      keys.every(
        (k) =>
          Object.prototype.hasOwnProperty.call(bo, k) && sameJson(ao[k], bo[k]),
      )
    );
  }
  return typeof a === typeof b && a === b;
}
