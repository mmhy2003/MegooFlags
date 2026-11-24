import Link from "next/link";

import type { components } from "@/lib/api/schema";
import { flagPath } from "@/lib/routes";
import { usageByFlag } from "@/lib/segments/segment-draft";

type SegmentUsage = components["schemas"]["SegmentUsage"];

/** The flags whose rules use a segment, each a link with the environments it is used in. */
export function UsedBy({
  project,
  usedBy,
}: {
  project: string;
  usedBy: SegmentUsage[];
}) {
  if (usedBy.length === 0)
    return <p className="text-muted-foreground">No flag uses this segment.</p>;
  return (
    <>
      <p className="text-muted-foreground">
        A saved change applies to these flags at once, in every environment
        listed, protected ones too.
      </p>
      <ul className="flex min-w-0 flex-col divide-y">
        {usageByFlag(usedBy).map(({ flag, environments }) => (
          <li
            key={flag}
            className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 py-1"
          >
            <Link
              href={flagPath(project, flag)}
              className="inline-flex min-h-11 items-center font-mono break-all hover:underline"
            >
              {flag}
            </Link>
            <span className="text-sm break-words text-muted-foreground">
              {environments.join(", ")}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}
