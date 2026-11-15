// The evaluate request context: the OpenFeature context mapped to JSON, its
// canonical form and the hash that keys the cache (spec/client/README.md).
import type {
  EvaluationContext,
  EvaluationContextValue,
  JsonValue,
} from "@openfeature/web-sdk";
import { sha256Hex } from "./sha256.js";

/**
 * toRequestContext copies every attribute as JSON. A non-empty targeting
 * key is written as key, replacing a key attribute; dates become UTC RFC
 * 3339 with milliseconds; undefined members are dropped.
 */
export function toRequestContext(
  ctx: EvaluationContext,
): Record<string, JsonValue> {
  const { targetingKey, ...attributes } = ctx;
  const body = toJsonObject(attributes);
  if (typeof targetingKey === "string" && targetingKey !== "") {
    body.key = targetingKey;
  }
  return body;
}

function toJsonObject(
  o: Record<string, EvaluationContextValue | undefined>,
): Record<string, JsonValue> {
  const out: Record<string, JsonValue> = {};
  for (const [k, v] of Object.entries(o)) {
    if (v !== undefined) out[k] = toJson(v);
  }
  return out;
}

function toJson(v: EvaluationContextValue): JsonValue {
  if (v instanceof Date) return v.toISOString();
  if (Array.isArray(v)) return v.map(toJson);
  if (v !== null && typeof v === "object") return toJsonObject(v);
  return v;
}

/** compareCodePoints orders strings by Unicode code point, not UTF-16 unit. */
function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const ca = ia.next();
    const cb = ib.next();
    if (ca.done || cb.done) return (ca.done ? 0 : 1) - (cb.done ? 0 : 1);
    const d = ca.value.codePointAt(0)! - cb.value.codePointAt(0)!;
    if (d !== 0) return d;
  }
}

/**
 * canonicalJson writes value without whitespace, object keys sorted by
 * code point and integral numbers as integers. JSON.stringify escapes
 * exactly ", \, \b \f \n \r \t and other control characters as \u00xx.
 */
export function canonicalJson(value: JsonValue): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort(compareCodePoints);
    return `{${keys
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k]!)}`)
      .join(",")}}`;
  }
  // JSON.stringify already writes integral numbers below 1e21 as integers.
  return JSON.stringify(value);
}

/** contextHash is the lowercase hex SHA-256 of canonicalJson(body). */
export function contextHash(body: Record<string, JsonValue>): string {
  return sha256Hex(canonicalJson(body));
}

/** targetingKeyOf is body.key when it is a string, else null. */
export function targetingKeyOf(body: Record<string, JsonValue>): string | null {
  return typeof body.key === "string" ? body.key : null;
}
