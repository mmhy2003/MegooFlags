import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill, type Status } from "@/components/patterns/status-pill";
import { kitEnabled } from "@/lib/kit";

import { DataListSample, RolloutSample, ToggleSample } from "./kit-samples";

const STATUSES: Status[] = ["on", "off", "stale", "killed", "pending", "error"];

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
      <PageHeader
        title="Component kit"
        description="Shared components in every state, for review and e2e checks."
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
