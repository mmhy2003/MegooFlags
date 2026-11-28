import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { components } from "@/lib/api/schema";
import { summary } from "@/lib/nodes/status";

type NodeList = components["schemas"]["NodeList"];

function SummaryCard({
  title,
  value,
  note,
}: {
  title: string;
  value: React.ReactNode;
  note?: string;
}) {
  return (
    <Card className="min-w-0 px-4">
      <CardHeader className="px-0">
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-2xl font-semibold tabular-nums">
          {value}
        </CardTitle>
      </CardHeader>
      {note && (
        <CardContent className="px-0 text-sm break-words text-muted-foreground">
          {note}
        </CardContent>
      )}
    </Card>
  );
}

/** The counts above the Nodes list, and the snapshot version workers should be on. */
export function NodeSummary({ list }: { list: NodeList }) {
  const counts = summary(list.nodes);
  return (
    <section
      aria-label="Summary"
      className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4"
    >
      <SummaryCard title="Workers" value={counts.workers} />
      <SummaryCard title="Healthy" value={counts.healthy} />
      <SummaryCard title="Need attention" value={counts.attention} />
      <SummaryCard
        title="Snapshot"
        value={`v${list.snapshot_version}`}
        note={`Control plane ${list.version}`}
      />
    </section>
  );
}
