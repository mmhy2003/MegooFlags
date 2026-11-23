import { bindingText } from "@/lib/access/bindings";
import type { components } from "@/lib/api/schema";

type Mapping = components["schemas"]["SsoMappingOut"];

/** "devs → team web" or "ops → viewer · Project shop". */
export function mappingText(m: Mapping): string {
  if (m.team !== null) return `${m.group} → team ${m.team}`;
  return `${m.group} → ${bindingText({
    role: m.role ?? "",
    scope: m.scope ?? "org",
    project: m.project,
    env: m.env,
  })}`;
}

/**
 * The mappings by group, then by what they give, then by id: the API lists them by their random
 * id, so without this the list would reorder itself.
 */
export function sortMappings(mappings: Mapping[]): Mapping[] {
  const text = new Map(mappings.map((m) => [m.id, mappingText(m)]));
  return [...mappings].sort(
    (a, b) =>
      a.group.localeCompare(b.group, "en") ||
      text.get(a.id)!.localeCompare(text.get(b.id)!, "en") ||
      a.id.localeCompare(b.id),
  );
}
