"use client";

import { useId, useState } from "react";

import { RolloutCard } from "@/components/flags/rollout-card";
import { RolloutDialog } from "@/components/flags/rollout-dialog";
import { ScheduleList } from "@/components/flags/schedule-list";
import type { useFlagAutomation } from "@/components/flags/use-flag-automation";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import type { components } from "@/lib/api/schema";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

type FlagOut = components["schemas"]["FlagOut"];
type Serve = components["schemas"]["Serve"];

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
  flag,
  env,
  envName,
  enabled,
  perms,
  needsApproval,
  fallthrough,
  automation,
  onApplied,
  onApprovalRequired,
}: {
  project: string;
  flag: FlagOut;
  env: string;
  envName: string;
  /** Whether the flag is on in the environment. */
  enabled: boolean;
  perms: MePermissions | null;
  needsApproval: boolean;
  /** The saved default rule; null while it loads. */
  fallthrough: Serve | null;
  automation: ReturnType<typeof useFlagAutomation>;
  /** An action here changed the flag: reload it. */
  onApplied: () => void;
  /** The API answered that the environment needs approval: reload environments. */
  onApprovalRequired: () => void;
}) {
  const { data, failure, reload } = automation;
  const [starting, setStarting] = useState(false);
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

  const changed = () => {
    void reload();
    onApplied();
  };
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Section title="Progressive rollout">
        <RolloutCard
          project={project}
          flag={flag.key}
          env={env}
          rollout={data.rollout}
          envName={envName}
          enabled={enabled}
          canWrite={!flag.archived && can(perms, "flag.config.write", env)}
          needsApproval={needsApproval}
          onStart={() => setStarting(true)}
          onChanged={changed}
        />
        <RolloutDialog
          project={project}
          flag={flag}
          env={env}
          envName={envName}
          needsApproval={needsApproval}
          fallthrough={fallthrough}
          open={starting}
          onOpenChange={setStarting}
          onDone={() => {
            setStarting(false);
            changed();
          }}
          onApprovalRequired={onApprovalRequired}
        />
      </Section>
      <Section title="Scheduled changes">
        <ScheduleList project={project} schedules={data.schedules} />
      </Section>
    </div>
  );
}
