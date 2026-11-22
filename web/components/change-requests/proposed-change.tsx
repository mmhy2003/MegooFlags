"use client";

import { DiffViewer } from "@/components/patterns/diff-viewer";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { currentSummary, proposedSummary } from "@/lib/change-requests/summary";
import { configText, toDraft } from "@/lib/flags/config-draft";
import { must } from "@/lib/flags/errors";

type S = components["schemas"];

/** A targeting request as a diff of the live targeting against the proposed one. */
function ConfigDiff({
  project,
  cr,
  current,
}: {
  project: string;
  cr: S["ChangeRequestOut"];
  current: S["FlagConfigOut"];
}) {
  const flag = useApiData(`flag:${project}:${cr.flag}`, () =>
    must(
      api.GET("/api/projects/{project}/flags/{flag}", {
        params: { path: { project, flag: cr.flag } },
      }),
    ),
  );
  if (flag.failure)
    return (
      <LoadFailed
        title="Couldn't load the flag"
        failure={flag.failure}
        onRetry={flag.reload}
      />
    );
  if (!flag.data) return <LoadingRows label="Loading the change" rows={1} />;
  const keys = flag.data.variations.map((v) => v.key);
  const proposed = { ...current, ...cr.proposed } as S["FlagConfigOut"];
  return (
    <DiffViewer
      label="Proposed change"
      before={configText(toDraft(current, keys))}
      after={configText(toDraft(proposed, keys))}
    />
  );
}

/**
 * What a change request would change: a diff for targeting, otherwise one sentence with the
 * flag's current state in the environment (`config`, null while it loads).
 */
export function ProposedChange({
  project,
  cr,
  config,
}: {
  project: string;
  cr: S["ChangeRequestOut"];
  config: S["FlagConfigOut"] | null;
}) {
  if (cr.kind === "config" && cr.current)
    return <ConfigDiff project={project} cr={cr} current={cr.current} />;
  return (
    <div className="flex flex-col gap-1">
      <p className="font-medium">{proposedSummary(cr.kind, cr.payload)}</p>
      {config && (
        <p className="text-muted-foreground">
          {currentSummary(cr.kind, config)}
        </p>
      )}
    </div>
  );
}
