"use client";

import { useId, useState } from "react";

import {
  bucketLabel,
  EvaluationChart,
  variantLabel,
} from "@/components/flags/evaluation-chart";
import { EmptyState } from "@/components/patterns/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { failureText, must } from "@/lib/flags/errors";

type EvaluationSeries = components["schemas"]["EvaluationSeries"];
type Range = "24h" | "7d" | "30d";

const RANGES: { value: Range; label: string }[] = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

const TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function isRange(value: string): value is Range {
  return RANGES.some((r) => r.value === value);
}

function SeriesTable({ series }: { series: EvaluationSeries }) {
  return (
    <Table>
      <TableCaption>Evaluations</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">
            {series.interval === "hour" ? "Hour" : "Day"}
          </TableHead>
          {series.variants.map((v) => (
            <TableHead key={v} scope="col">
              {variantLabel(v)}
            </TableHead>
          ))}
          <TableHead scope="col">Total</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {series.points.map((p) => (
          <TableRow key={p.start}>
            <TableHead scope="row">
              <time dateTime={p.start}>
                {bucketLabel(series.interval, p.start)}
              </time>
            </TableHead>
            {series.variants.map((v) => (
              <TableCell key={v}>{p.counts[v] ?? 0}</TableCell>
            ))}
            <TableCell>
              {Object.values(p.counts).reduce((sum, n) => sum + n, 0)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/**
 * A flag's evaluations in one environment over a chosen range, as a stacked-bar chart or a
 * table, and when the flag was last evaluated anywhere.
 */
export function InsightsPanel({
  project,
  flag,
  env,
}: {
  project: string;
  flag: components["schemas"]["FlagOut"];
  env: string;
}) {
  const id = useId();
  const [range, setRange] = useState<Range>("24h");
  const [asTable, setAsTable] = useState(false);
  const series = useApiData(
    env ? `evaluations:${project}/${flag.key}/${env}/${range}` : null,
    () =>
      must(
        api.GET(
          "/api/projects/{project}/flags/{flag}/environments/{env}/evaluations",
          {
            params: {
              path: { project, flag: flag.key, env },
              query: { range },
            },
          },
        ),
      ),
  );

  let body: React.ReactNode;
  if (series.failure)
    body = (
      <Alert variant="destructive">
        <AlertTitle>Couldn&apos;t load evaluations</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>{failureText(series.failure)}</p>
          <Button variant="outline" onClick={series.reload}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  else if (!series.data)
    body = (
      <div role="status">
        <span className="sr-only">Loading evaluations</span>
        <Skeleton className="h-48 w-full" />
      </div>
    );
  else if (series.data.total === 0)
    body = <EmptyState title="No evaluations in this range" />;
  else
    body = (
      <>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-lg font-semibold">
            {series.data.total} evaluations
          </p>
          <Button
            variant="outline"
            className="min-h-11"
            aria-pressed={asTable}
            onClick={() => setAsTable((t) => !t)}
          >
            {asTable ? "Show as chart" : "Show as table"}
          </Button>
        </div>
        {asTable ? (
          <SeriesTable series={series.data} />
        ) : (
          <EvaluationChart series={series.data} />
        )}
      </>
    );

  return (
    <section className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-range`}>Range</Label>
          <NativeSelect
            id={`${id}-range`}
            value={range}
            onChange={(e) => {
              if (isRange(e.target.value)) setRange(e.target.value);
            }}
          >
            {RANGES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <p className="text-muted-foreground">
          {flag.last_evaluated_at ? (
            <>
              Last evaluated{" "}
              <time dateTime={flag.last_evaluated_at}>
                {TIME.format(new Date(flag.last_evaluated_at))}
              </time>
            </>
          ) : (
            "Never evaluated"
          )}
        </p>
      </div>
      {body}
    </section>
  );
}
