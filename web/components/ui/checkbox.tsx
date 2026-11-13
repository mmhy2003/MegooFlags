import { cn } from "@/lib/utils";

/**
 * A native checkbox. Render it inside
 * `<label data-hit-area className="inline-flex min-h-11 min-w-11 items-center gap-2">` so the
 * target is 44 px.
 */
export function Checkbox({
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "type">) {
  return (
    <input
      type="checkbox"
      data-slot="checkbox"
      className={cn("size-5 shrink-0 accent-primary", className)}
      {...props}
    />
  );
}
