import { ShieldAlert } from "lucide-react";

/** DEP-6: tells the visitor that this connection is plain HTTP. */
export function HttpWarning({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2 border-b border-warning bg-warning/10 px-4 py-2 text-sm text-foreground"
    >
      <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
      <p>
        Plain HTTP: this connection is not encrypted, so session cookies and SDK
        keys can be read on the network. Serve MegooFlags over HTTPS.
      </p>
    </div>
  );
}
