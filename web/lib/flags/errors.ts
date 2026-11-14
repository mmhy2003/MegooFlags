/** A failed API call, reduced to what the UI shows. */
export type ApiFailure = {
  status: number;
  code: string;
  message: string;
  /** Where in the request the problem is, such as "rules[0].clauses[1].values[0]". */
  path: string | null;
};

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function locPath(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const parts = loc[0] === "body" || loc[0] === "query" ? loc.slice(1) : loc;
  let path = "";
  for (const part of parts) {
    if (typeof part === "number") path += `[${part}]`;
    else path += path ? `.${String(part)}` : String(part);
  }
  return path || null;
}

/** Reads an API error body; a body that isn't one gives code "unknown" and "HTTP <status>". */
export function toFailure(status: number, body: unknown): ApiFailure {
  if (
    !isObj(body) ||
    typeof body.code !== "string" ||
    typeof body.message !== "string"
  ) {
    return { status, code: "unknown", message: `HTTP ${status}`, path: null };
  }
  const { details } = body;
  if (Array.isArray(details) && isObj(details[0])) {
    const first = details[0];
    return {
      status,
      code: body.code,
      message: typeof first.msg === "string" ? first.msg : body.message,
      path: locPath(first.loc),
    };
  }
  const path =
    isObj(details) && typeof details.path === "string" ? details.path : null;
  return { status, code: body.code, message: body.message, path };
}

/** A short sentence for a failure. */
export function failureText(f: ApiFailure): string {
  if (f.code === "approval_required") return "Needs approval";
  if (f.status === 403) return "You don't have permission";
  if (f.code === "version_conflict") return "Someone saved a newer version";
  if (f.status === 404) return "Not found";
  return f.message;
}

/** Thrown by `must` when the API answers with an error. */
export class ApiError extends Error {
  readonly failure: ApiFailure;

  constructor(failure: ApiFailure) {
    super(failure.message);
    this.name = "ApiError";
    this.failure = failure;
  }
}

/** Unwraps an `api` call: its data, or an `ApiError`. */
export async function must<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined) {
    throw new ApiError(toFailure(response.status, error));
  }
  return data as T;
}
