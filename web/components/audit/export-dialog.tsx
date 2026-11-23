"use client";

import { Download } from "lucide-react";
import { useId, useState } from "react";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  type AuditFilterValues,
  type AuditScope,
  exportUrl,
} from "@/lib/audit/filters";

type Format = "csv" | "ndjson";

const FORMATS: { value: Format; label: string }[] = [
  { value: "csv", label: "CSV" },
  { value: "ndjson", label: "NDJSON" },
];

/**
 * Exports the entries the filters match, as CSV or NDJSON. The download is a plain link, so
 * the browser sends the session cookie and streams the file itself. With `unavailable` (the
 * list failed with these filters) there is no link: the export would fail the same way, and the
 * browser would save the error as the file.
 */
export function ExportDialog({
  filters,
  scope,
  unavailable = false,
}: {
  filters: AuditFilterValues;
  scope: AuditScope;
  unavailable?: boolean;
}) {
  const id = useId();
  const [format, setFormat] = useState<Format>("csv");

  return (
    <ResponsiveDialog
      trigger={
        <Button variant="outline">
          <Download aria-hidden />
          Export
        </Button>
      }
      title="Export audit log"
      description="Exports every entry that matches the current filters."
      footer={
        unavailable ? (
          <Button disabled>
            <Download aria-hidden />
            Download
          </Button>
        ) : (
          <a
            href={exportUrl(filters, scope, format)}
            download
            className={buttonVariants()}
          >
            <Download aria-hidden />
            Download
          </a>
        )
      }
    >
      {unavailable && (
        <p className="text-muted-foreground">
          The audit log couldn&apos;t load, so there is nothing to export yet.
        </p>
      )}
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">Format</legend>
        <div
          role="radiogroup"
          aria-label="Format"
          className="flex flex-wrap gap-x-6"
        >
          {FORMATS.map((f) => (
            <label
              key={f.value}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <input
                type="radio"
                name={`${id}-format`}
                className="size-5 shrink-0 accent-primary"
                checked={format === f.value}
                onChange={() => setFormat(f.value)}
              />
              {f.label}
            </label>
          ))}
        </div>
      </fieldset>
    </ResponsiveDialog>
  );
}
