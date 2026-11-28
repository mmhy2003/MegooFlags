import type { components } from "@/lib/api/schema";

type S = components["schemas"];
type JsonValue = S["JsonValue"];

/** One variation as typed in the form; `value` is the text in the value field. */
export type VariationRow = { key: string; name: string; value: string };

/** The New flag form. Boolean flags ignore `variations` and `offVariation`. */
export type FlagForm = {
  key: string;
  /** True once the user typed a key, so the name stops filling it in. */
  keyEdited: boolean;
  name: string;
  description: string;
  type: S["FlagType"];
  tags: string[];
  owner: string;
  variations: VariationRow[];
  offVariation: string;
};

/** The API's pattern for flag and variation keys. */
export const KEY_PATTERN = /^[a-z0-9][a-z0-9._-]{0,99}$/;

const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

/** A key made from a name: lowercase, other characters as "-", no leading "-._", at most 100 characters. */
export function slugifyKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[-._]+/, "")
    .slice(0, 100)
    .replace(/-+$/, "");
}

/** Tags from comma-separated text: trimmed, without empties or repeats. */
export function parseTags(text: string): string[] {
  const tags = text
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  return [...new Set(tags)];
}

/** Reads a variation value typed for a flag type; strings are kept as they are. */
export function parseVariationValue(
  type: S["FlagType"],
  text: string,
): { ok: true; value: JsonValue } | { ok: false; error: string } {
  if (type === "string") return { ok: true, value: text };
  if (type === "number") {
    const value = Number(text);
    return text.trim() !== "" && Number.isFinite(value)
      ? { ok: true, value }
      : { ok: false, error: "Enter a number" };
  }
  try {
    return { ok: true, value: JSON.parse(text) as JsonValue };
  } catch {
    return { ok: false, error: "Enter valid JSON" };
  }
}

/** A variation value as text for its field: strings raw, the rest as JSON. */
export function formatVariationValue(
  type: S["FlagType"],
  value: JsonValue,
): string {
  return type === "string" && typeof value === "string"
    ? value
    : JSON.stringify(value);
}

/** Problems with the form, by field: "key", "name", "variations[i].key", "variations[i].value", "offVariation". */
export function formErrors(f: FlagForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!KEY_PATTERN.test(f.key)) errors.key = KEY_HINT;
  if (!f.name.trim()) errors.name = "Enter a name";
  if (f.type === "boolean") return errors;

  const seen = new Set<string>();
  f.variations.forEach((v, i) => {
    if (!KEY_PATTERN.test(v.key)) errors[`variations[${i}].key`] = KEY_HINT;
    else if (seen.has(v.key))
      errors[`variations[${i}].key`] = "This key is already used";
    seen.add(v.key);
    const parsed = parseVariationValue(f.type, v.value);
    if (!parsed.ok) errors[`variations[${i}].value`] = parsed.error;
  });
  if (!f.variations.some((v) => v.key === f.offVariation)) {
    errors.offVariation = "Choose one of the variations";
  }
  return errors;
}

/** The create request for a valid form; boolean flags get their variations from the API. */
export function toFlagCreate(f: FlagForm): S["FlagCreate"] {
  const create: S["FlagCreate"] = {
    key: f.key,
    name: f.name.trim(),
    description: f.description,
    type: f.type,
    tags: f.tags,
    owner: f.owner.trim() || null,
  };
  if (f.type === "boolean") return create;
  return {
    ...create,
    variations: f.variations.map((v) => {
      const parsed = parseVariationValue(f.type, v.value);
      return {
        key: v.key,
        name: v.name,
        description: "",
        value: parsed.ok ? parsed.value : null,
      };
    }),
    off_variation: f.offVariation,
  };
}
