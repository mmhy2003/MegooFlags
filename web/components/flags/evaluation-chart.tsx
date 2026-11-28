import type { components } from "@/lib/api/schema";

type EvaluationSeries = components["schemas"]["EvaluationSeries"];

// Literal class names, so Tailwind generates them from the --color-chart-N tokens.
const FILLS = [
  "fill-chart-1",
  "fill-chart-2",
  "fill-chart-3",
  "fill-chart-4",
  "fill-chart-5",
];
const SWATCHES = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
];

const HOUR = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});
const DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

/** A bucket start, as an hour or a day. */
export function bucketLabel(
  interval: EvaluationSeries["interval"],
  start: string,
): string {
  return (interval === "hour" ? HOUR : DAY).format(new Date(start));
}

/** A variant's display name; "" counts evaluations with no variant. */
export function variantLabel(variant: string): string {
  return variant === "" ? "No variant" : variant;
}

function bucketTotal(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, n) => sum + n, 0);
}

/**
 * Evaluations per bucket as stacked bars, one colour per variant, with a legend and the first
 * and last bucket below. Each bar is 8 units wide in a 10-unit slot, scaled to the busiest
 * bucket.
 */
export function EvaluationChart({ series }: { series: EvaluationSeries }) {
  const { interval, variants, points, total } = series;
  const max = Math.max(1, ...points.map((p) => bucketTotal(p.counts)));
  const first = points[0];
  const last = points[points.length - 1];
  const range =
    first && last
      ? `${bucketLabel(interval, first.start)} to ${bucketLabel(interval, last.start)}`
      : "";

  return (
    <figure
      aria-label="Evaluations by variation"
      className="flex min-w-0 flex-col gap-3"
    >
      <svg
        role="img"
        aria-label={`${total} evaluations, ${range}`}
        viewBox={`0 0 ${points.length * 10} 100`}
        preserveAspectRatio="none"
        width="100%"
        className="h-48"
      >
        {points.map((p, i) => {
          let top = 100;
          return (
            <g key={p.start}>
              {variants.map((v, j) => {
                const count = p.counts[v] ?? 0;
                if (count === 0) return null;
                const height = (count / max) * 100;
                top -= height;
                return (
                  <rect
                    key={v}
                    x={i * 10 + 1}
                    y={top}
                    width={8}
                    height={height}
                    className={FILLS[j % FILLS.length]}
                  />
                );
              })}
            </g>
          );
        })}
      </svg>
      {first && last && (
        <div className="flex justify-between gap-2 text-sm text-muted-foreground">
          <time dateTime={first.start}>
            {bucketLabel(interval, first.start)}
          </time>
          <time dateTime={last.start}>{bucketLabel(interval, last.start)}</time>
        </div>
      )}
      <figcaption>
        <ul aria-label="Variations" className="flex flex-wrap gap-x-4 gap-y-1">
          {variants.map((v, j) => (
            <li key={v} className="flex items-center gap-2">
              <span
                aria-hidden
                className={`size-3 rounded-sm ${SWATCHES[j % SWATCHES.length]}`}
              />
              {variantLabel(v)}
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}
