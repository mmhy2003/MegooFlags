/** Avatar fallback: up to two initials from the name, else the email's first letter, else "?". */
export function initials(
  name: string | null | undefined,
  email: string | null | undefined,
): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 0) {
    return words
      .slice(0, 2)
      .map((word) => word[0].toUpperCase())
      .join("");
  }
  const first = (email ?? "").trim()[0];
  return first ? first.toUpperCase() : "?";
}
