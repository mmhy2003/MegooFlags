// Stored evaluate results mapped to OpenFeature resolution details
// (spec/client/README.md, resolution cases).
import {
  ErrorCode,
  StandardResolutionReasons,
  type JsonValue,
  type ResolutionDetails,
} from "@openfeature/web-sdk";
import type { StoredResults } from "./store.js";

/** The OpenFeature read the value is resolved for. */
export type FlagType = "boolean" | "string" | "number" | "object";

/** Server error codes that keep their name; any other becomes GENERAL. */
const SERVER_CODES: ReadonlySet<string> = new Set([
  ErrorCode.FLAG_NOT_FOUND,
  ErrorCode.PARSE_ERROR,
  ErrorCode.TYPE_MISMATCH,
  ErrorCode.TARGETING_KEY_MISSING,
  ErrorCode.INVALID_CONTEXT,
  ErrorCode.GENERAL,
]);

function errorCodeOf(code: string): ErrorCode {
  return SERVER_CODES.has(code) ? (code as ErrorCode) : ErrorCode.GENERAL;
}

function hasType(value: JsonValue, type: FlagType): boolean {
  if (type === "object") return value !== null && typeof value === "object";
  return typeof value === type;
}

function failed<T>(
  defaultValue: T,
  errorCode: ErrorCode,
  errorMessage?: string | null,
): ResolutionDetails<T> {
  const details: ResolutionDetails<T> = {
    value: defaultValue,
    reason: StandardResolutionReasons.ERROR,
    errorCode,
  };
  if (errorMessage != null) details.errorMessage = errorMessage;
  return details;
}

/**
 * resolveFlag reads flag from the stored results as type. Errors return the
 * default with reason ERROR; a null variant returns the default with the
 * stored reason; values from the cache have reason CACHED. Object reads
 * accept plain objects and arrays.
 */
export function resolveFlag<T>(
  stored: StoredResults | undefined,
  flag: string,
  type: FlagType,
  defaultValue: T,
): ResolutionDetails<T> {
  if (!stored) return failed(defaultValue, ErrorCode.PROVIDER_NOT_READY);
  const r = stored.results.get(flag);
  if (!r) return failed(defaultValue, ErrorCode.FLAG_NOT_FOUND);
  if (r.error_code !== null) {
    return failed(defaultValue, errorCodeOf(r.error_code), r.error_message);
  }
  if (r.variant === null) return { value: defaultValue, reason: r.reason };
  if (!hasType(r.value, type)) {
    return failed(defaultValue, ErrorCode.TYPE_MISMATCH);
  }
  return {
    value: r.value as T,
    variant: r.variant,
    reason:
      stored.source === "cache" ? StandardResolutionReasons.CACHED : r.reason,
  };
}
