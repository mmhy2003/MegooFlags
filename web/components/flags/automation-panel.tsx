"use client";

import { useId } from "react";

import { RolloutCard } from "@/components/flags/rollout-card";
import { ScheduleList } from "@/components/flags/schedule-list";
import type { useFlagAutomation } from "@/components/flags/use-flag-automation";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-4"
    >
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** The Automation tab: the environment's progressive rollout and its scheduled changes. */
export function AutomationPanel({
  project,
  envName,
  enabled,
  automation,
}: {
  project: string;
  envName: string;
  /** Whether the flag is on in the environment. */
  enabled: boolean;
  automation: ReturnType<typeof useFlagAutomation>;
}) {
  const { data, failure, reload } = automation;
  if (failure)
    return (
      <LoadFailed
        title="Couldn't load automation"
        failure={failure}
        onRetry={() => void reload()}
      />
    );
  if (!data.rollout || !data.schedules)
    return <LoadingRows label="Loading automation" rows={2} />;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Section title="Progressive rollout">
        <RolloutCard
          rollout={data.rollout}
          envName={envName}
          enabled={enabled}
        />
      </Section>
      <Section title="Scheduled changes">
        <ScheduleList project={project} schedules={data.schedules} />
      </Section>
    </div>
  );
}
