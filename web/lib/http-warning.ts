const LOOPBACK = new Set(["localhost", "127.0.0.1", "::1"]);

function hostname(host: string): string {
  const bracketed = host.match(/^\[([^\]]+)\]/);
  if (bracketed) return bracketed[1];
  return host.replace(/:\d+$/, "").toLowerCase();
}

/** Warn when the page is served over plain HTTP to anything but this machine. */
export function shouldWarnPlainHttp({
  forwardedProto,
  host,
}: {
  forwardedProto: string | null;
  host: string | null;
}): boolean {
  if (!host) return false;
  const scheme = forwardedProto?.split(",")[0].trim().toLowerCase() || "http";
  return scheme === "http" && !LOOPBACK.has(hostname(host));
}
