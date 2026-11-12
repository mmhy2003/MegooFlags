import Link from "next/link";
import { ChevronRight } from "lucide-react";

import type { Crumb } from "@/lib/routes";

/** A page's h1, with an optional breadcrumb trail above it and actions beside it. */
export function PageHeader({
  title,
  description,
  trail,
  actions,
}: {
  title: string;
  description?: React.ReactNode;
  trail?: Crumb[];
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-2">
      {trail && trail.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol className="flex flex-wrap items-center gap-x-1 text-sm text-muted-foreground">
            {trail.map((crumb, i) => (
              <li key={crumb.href} className="flex min-w-0 items-center gap-1">
                {i > 0 && <ChevronRight aria-hidden className="size-4" />}
                <Link
                  href={crumb.href}
                  className="inline-flex min-h-11 min-w-0 items-center break-all hover:text-foreground hover:underline"
                >
                  {crumb.label}
                </Link>
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-2xl font-semibold break-words">{title}</h1>
          {description && (
            <p className="text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
    </header>
  );
}
