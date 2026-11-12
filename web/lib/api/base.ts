/** Where the server side reaches the API (the browser uses same-origin /api). */
export function apiBaseUrl(): string {
  return process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000";
}
