import { cn } from "cn";

/** The diamond mark; colours come from the theme tokens, so it has light and dark variants. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden
      className={cn("size-8 shrink-0", className)}
    >
      <path className="fill-accent" d="M16 2 30 16 16 30 2 16Z" />
      <path className="fill-primary" d="M16 8 24 16 16 24 8 16Z" />
    </svg>
  );
}

/** The logo and wordmark; the wordmark hides when the sidebar is collapsed to icons. */
export function Brand({ className }: { className?: string }) {
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      <BrandMark />
      <span className="truncate text-lg font-semibold tracking-tight group-data-[collapsible=icon]:hidden">
        MegooFlags
      </span>
    </span>
  );
}
