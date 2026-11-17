import { ApiError, failureText } from "@/lib/flags/errors";

/** The API's NodeCreate pattern; the length limit is checked separately for its own message. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const NAME_MAX = 100;

/** Why `name` can't name a new node, or undefined when it can. */
export function nameError(name: string): string | undefined {
  if (name.trim() === "") return "Enter a name";
  if (name.length > NAME_MAX) return `Use at most ${NAME_MAX} characters`;
  if (!NAME_PATTERN.test(name)) {
    return "Use letters, digits, dots, dashes or underscores";
  }
  return undefined;
}

/** Where a failed Add lands: under the Name field, or in a toast. */
export function submitError(
  error: unknown,
): { field: string } | { toast: string } {
  if (!(error instanceof ApiError)) {
    return { toast: "Couldn't reach the server" };
  }
  const { failure } = error;
  if (failure.status === 409 && failure.code === "key_conflict") {
    return { field: "A node with this name already exists" };
  }
  if (failure.status === 422) return { field: failure.message };
  return { toast: failureText(failure) };
}

/** How long the old token keeps working after a rotation. */
export const OVERLAPS: { seconds: number; label: string }[] = [
  { seconds: 0, label: "Revoke now" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 86400, label: "1 day" },
  { seconds: 604800, label: "7 days" },
];

export const DEFAULT_OVERLAP = 86400;
