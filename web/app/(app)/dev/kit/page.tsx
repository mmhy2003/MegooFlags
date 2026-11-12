import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { Flag } from "lucide-react";

import { DiffViewer } from "@/components/patterns/diff-viewer";
import { EmptyState } from "@/components/patterns/empty-state";
import { JsonViewer } from "@/components/patterns/json-viewer";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill, type Status } from "@/components/patterns/status-pill";
import { kitEnabled } from "@/lib/kit";

import {
  ConfirmSample,
  DataListSample,
  DialogSample,
  RolloutSample,
  ToggleSample,
} from "./kit-samples";

const STATUSES: Status[] = ["on", "off", "stale", "killed", "pending", "error"];

const SAMPLE_JSON = {
  key: "new-checkout",
  enabled: true,
  rollout: { variation: "on", weight: 25.5 },
  rules: [{ attribute: "country", op: "in", values: ["EG", "DE"] }],
  note: "x".repeat(200),
};

const DIFF_BEFORE = JSON.stringify(
  { key: "new-checkout", enabled: false, rollout: 10 },
  null,
  2,
);
const DIFF_AFTER = JSON.stringify(
  { key: "new-checkout", enabled: true, rollout: 25, owner: "Ada Admin" },
  null,
  2,
);

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

async function Kit() {
  // Read the flag per request, not at build time.
  await connection();
  if (!kitEnabled(process.env.MEGOOFLAGS_UI_KIT)) notFound();
  return (
    <>
      {/* The page's own header doubles as the page header sample. */}
      <PageHeader
        title="Component kit"
        description="Shared components in every state, for review and e2e checks."
        trail={[{ label: "Home", href: "/" }]}
      />
      <div className="flex flex-col gap-8">
        <Section id="kit-status-pills" title="Status pills">
          <div className="flex flex-wrap gap-2">
            {STATUSES.map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
        </Section>
        <Section id="kit-data-list" title="Data list">
          <DataListSample />
        </Section>
        <Section id="kit-toggles" title="Toggles">
          <ToggleSample />
        </Section>
        <Section id="kit-rollout" title="Rollout slider">
          <RolloutSample />
        </Section>
        <Section id="kit-dialog" title="Dialog">
          <div>
            <DialogSample />
          </div>
        </Section>
        <Section id="kit-confirm" title="Confirm dialog">
          <ConfirmSample />
        </Section>
        <Section id="kit-json" title="JSON viewer">
          <JsonViewer value={SAMPLE_JSON} label="Sample flag JSON" />
        </Section>
        <Section id="kit-diff" title="Diff viewer">
          <DiffViewer
            before={DIFF_BEFORE}
            after={DIFF_AFTER}
            label="Sample flag change"
          />
        </Section>
        <Section id="kit-empty" title="Empty state">
          <EmptyState
            icon={Flag}
            title="No flags yet"
            description="Create a flag to start rolling out changes safely."
          />
        </Section>
      </div>
    </>
  );
}

/** The component gallery, served only when MEGOOFLAGS_UI_KIT=1. */
export default function KitPage() {
  return (
    <Suspense fallback={null}>
      <Kit />
    </Suspense>
  );
}
