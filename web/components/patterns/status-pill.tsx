import {
  CircleAlert,
  CircleCheck,
  CircleOff,
  Clock,
  OctagonX,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { cn } from "cn";

export type Status = "on" | "off" | "stale" | "killed" | "pending" | "error";

// Light tints drop the text under 4.5:1, so only the dark theme gets a fill.
const TONES = {
  success: "border-success/40 text-success dark:bg-success/10",
  neutral: "border-border bg-muted text-muted-foreground",
  warning: "border-warning/40 text-warning dark:bg-warning/10",
  destructive: "border-destructive/40 text-destructive dark:bg-destructive/10",
  secondary: "border-secondary/40 text-secondary dark:bg-secondary/10",
} as const;

const STATUSES: Record<
  Status,
  { icon: LucideIcon; tone: keyof typeof TONES; label: string }
> = {
  on: { icon: CircleCheck, tone: "success", label: "On" },
  off: { icon: CircleOff, tone: "neutral", label: "Off" },
  stale: { icon: TriangleAlert, tone: "warning", label: "Stale" },
  killed: { icon: OctagonX, tone: "destructive", label: "Killed" },
  pending: { icon: Clock, tone: "secondary", label: "Pending" },
  error: { icon: CircleAlert, tone: "destructive", label: "Error" },
};

/** A status shown by icon and text as well as colour (UI-4). */
export function StatusPill({
  status,
  label,
  className,
}: {
  status: Status;
  label?: string;
  className?: string;
}) {
  const { icon: Icon, tone, label: fallback } = STATUSES[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-sm font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {status === "on" && (
        <span
          aria-hidden
          className="size-1.5 rounded-full bg-current motion-safe:animate-pulse dark:shadow-glow"
        />
      )}
      <Icon aria-hidden className="size-4 shrink-0" />
      <span>{label ?? fallback}</span>
    </span>
  );
}
