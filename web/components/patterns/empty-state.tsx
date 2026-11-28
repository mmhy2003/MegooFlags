import type { LucideIcon } from "lucide-react";

/** A centred message for a page or list with nothing to show yet. */
export function EmptyState({
  title,
  description,
  icon: Icon,
  headingLevel = 2,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  icon?: LucideIcon;
  /** 1 when the empty state is the whole page, so it carries the page's h1. */
  headingLevel?: 1 | 2;
  children?: React.ReactNode;
}) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed px-4 py-12 text-center">
      {Icon && <Icon aria-hidden className="size-8 text-muted-foreground" />}
      <Heading className="text-lg font-semibold">{title}</Heading>
      {description && (
        <p className="max-w-prose text-muted-foreground">{description}</p>
      )}
      {children}
    </div>
  );
}
