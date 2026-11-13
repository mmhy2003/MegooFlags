import { ApiError, failureText } from "@/lib/flags/errors";

export type BulkResult =
  { key: string; ok: true } | { key: string; ok: false; reason: string };

/** Runs `action` for every key, at most `limit` at a time; results are in input order. */
export async function runBulk(
  keys: string[],
  action: (key: string) => Promise<unknown>,
  limit = 4,
  onDone?: (result: BulkResult) => void,
): Promise<BulkResult[]> {
  const results: BulkResult[] = new Array(keys.length);
  let next = 0;
  async function worker() {
    while (next < keys.length) {
      const index = next++;
      const key = keys[index];
      let result: BulkResult;
      try {
        await action(key);
        result = { key, ok: true };
      } catch (e) {
        const reason =
          e instanceof ApiError ? failureText(e.failure) : String(e);
        result = { key, ok: false, reason };
      }
      results[index] = result;
      onDone?.(result);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, keys.length) }, worker),
  );
  return results;
}

/** A flag's tags after adding or removing one, or null when nothing changes. */
export function tagsAfter(
  tags: string[],
  tag: string,
  op: "add" | "remove",
): string[] | null {
  const has = tags.includes(tag);
  if (op === "add") return has ? null : [...tags, tag];
  return has ? tags.filter((t) => t !== tag) : null;
}
