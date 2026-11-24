"use client";

import { Pencil } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { KeyListField } from "@/components/flags/key-list-field";
import { UnsavedBar } from "@/components/flags/unsaved-bar";
import { useUnsavedGuard } from "@/components/flags/use-unsaved-guard";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { SegmentDetailsDialog } from "@/components/segments/segment-details-dialog";
import { SegmentRules } from "@/components/segments/segment-rules";
import { UsedBy } from "@/components/segments/used-by";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import type { DraftProblem } from "@/lib/flags/config-draft";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import { projectPath, projectTrail, segmentPath } from "@/lib/routes";
import {
  isDirty,
  problemField,
  type SegmentDraft,
  setKeys,
  toConfigIn,
  toDraft,
  validateDraft,
} from "@/lib/segments/segment-draft";

type SegmentOut = components["schemas"]["SegmentOut"];

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

/** Problems by the field that shows them; the first one wins. */
function byField(problems: DraftProblem[]): Record<string, string> {
  const fields: Record<string, string> = {};
  for (const p of problems) {
    const field = problemField(p.path);
    if (field && !(field in fields)) fields[field] = p.message;
  }
  return fields;
}

/**
 * One segment's page: its header, the included and excluded keys and the rules (a draft saved
 * with the unsaved-changes bar), and the flags that use it.
 */
export function SegmentDetail({
  project,
  segment,
}: {
  project: string;
  segment: string;
}) {
  const perms = useProjectPermissions(project, null, null);
  const canWrite = can(perms, "segment.write");

  const loadedSegment = useApiData(`segment:${project}/${segment}`, () =>
    must(
      api.GET("/api/projects/{project}/segments/{segment}", {
        params: { path: { project, segment } },
      }),
    ),
  );
  const usage = useApiData(`segment-usage:${project}/${segment}`, () =>
    must(
      api.GET("/api/projects/{project}/segments/{segment}/usage", {
        params: { path: { project, segment } },
      }),
    ),
  );
  // The segment as last saved here (keys and rules, or details), shown over the loaded one.
  const [saved, setSaved] = useState<SegmentOut | null>(null);
  const current = saved ?? loadedSegment.data;

  const loaded = useMemo(
    () => (loadedSegment.data ? toDraft(loadedSegment.data) : null),
    [loadedSegment.data],
  );
  // The saved keys and rules (base) and the draft edited from them. A new load resets both; a
  // save moves the base on.
  const [edit, setEdit] = useState<{
    loaded: SegmentDraft | null;
    base: SegmentDraft | null;
    draft: SegmentDraft | null;
  }>({ loaded: null, base: null, draft: null });
  if (edit.loaded !== loaded) setEdit({ loaded, base: loaded, draft: loaded });
  const { base, draft } = edit;
  const dirty = !!base && !!draft && isDirty(base, draft);
  useUnsavedGuard(dirty);

  // Client checks show once Save was pressed; an API 422 shows until the next edit.
  const [checked, setChecked] = useState(false);
  const [invalid, setInvalid] = useState<DraftProblem | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingDetails, setEditingDetails] = useState(false);

  const problems = [
    ...(checked && draft ? validateDraft(draft) : []),
    ...(invalid ? [invalid] : []),
  ];
  const fields = byField(problems);

  const setDraft = (next: SegmentDraft) => {
    setEdit((e) => ({ ...e, draft: next }));
    setInvalid(null);
  };
  const reset = (next: SegmentDraft | null) => {
    setEdit((e) => ({ ...e, base: next, draft: next }));
    setChecked(false);
    setInvalid(null);
  };

  const usedBy = usage.data?.used_by ?? null;

  const send = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const out = await must(
        api.PUT("/api/projects/{project}/segments/{segment}", {
          params: { path: { project, segment } },
          body: toConfigIn(draft),
        }),
      );
      setSaved(out);
      reset(toDraft(out));
      usage.reload();
      toast("Segment saved");
    } catch (error) {
      if (!(error instanceof ApiError)) {
        console.error("saving the segment failed", error);
        toast.error("Couldn't reach the server");
        return;
      }
      const f = error.failure;
      if (f.status === 422 && problemField(f.path))
        setInvalid({ path: f.path ?? "", message: f.message });
      else toast.error(failureText(f));
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    if (!draft || saving) return;
    setChecked(true);
    setInvalid(null);
    if (validateDraft(draft).length > 0) return;
    // Ask first unless the segment is known to be unused.
    if (usedBy === null || usedBy.length > 0) setConfirming(true);
    else void send();
  };

  const name = current?.name ?? segment;
  const header = (
    <PageHeader
      title={name}
      description={
        <>
          <code className="break-all">{segment}</code>
          {current?.description && (
            <span className="block break-words">{current.description}</span>
          )}
        </>
      }
      trail={[
        ...projectTrail(project),
        { label: "Segments", href: projectPath(project, "segments") },
        { label: name, href: segmentPath(project, segment) },
      ]}
      actions={
        canWrite && current ? (
          <Button variant="outline" onClick={() => setEditingDetails(true)}>
            <Pencil aria-hidden />
            Edit details
          </Button>
        ) : undefined
      }
    />
  );

  if (loadedSegment.failure)
    return (
      <>
        {header}
        <LoadFailed
          title="Couldn't load the segment"
          failure={loadedSegment.failure}
          onRetry={loadedSegment.reload}
        />
      </>
    );
  if (!current || !base || !draft)
    return (
      <>
        {header}
        <LoadingRows label="Loading the segment" />
      </>
    );

  const readOnly = !canWrite;
  const unplaced = problems.filter((p) => !problemField(p.path));

  return (
    <>
      {header}
      <div className="flex min-w-0 flex-col gap-4">
        {unplaced.length > 0 && (
          <Alert variant="destructive">
            <AlertTitle>Fix these before saving</AlertTitle>
            <AlertDescription>
              <ul className="list-disc pl-5">
                {unplaced.map((p) => (
                  <li key={`${p.path}:${p.message}`}>{p.message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        <Section title="Keys">
          <p className="text-muted-foreground">
            Included keys are always in the segment and excluded keys never are,
            whatever the rules say.
          </p>
          <div className="grid min-w-0 gap-4 md:grid-cols-2">
            {(["included", "excluded"] as const).map((list) => (
              <KeyListField
                key={list}
                label={list === "included" ? "Included keys" : "Excluded keys"}
                keys={draft[list]}
                readOnly={readOnly}
                showCount
                error={fields[list]}
                onApply={(keys) => {
                  const next = setKeys(draft, list, keys);
                  if (next[list].join("\n") !== draft[list].join("\n"))
                    setDraft(next);
                }}
              />
            ))}
          </div>
        </Section>
        <Section title="Rules">
          <SegmentRules
            draft={draft}
            onChange={setDraft}
            readOnly={readOnly}
            problems={fields}
          />
        </Section>
        <Section title="Used by">
          {usage.failure ? (
            <LoadFailed
              title="Couldn't load the flags that use this segment"
              failure={usage.failure}
              onRetry={usage.reload}
            />
          ) : usedBy === null ? (
            <LoadingRows label="Loading the flags that use this segment" />
          ) : (
            <UsedBy project={project} usedBy={usedBy} />
          )}
        </Section>
        {!readOnly && (
          <UnsavedBar
            isDirty={dirty}
            needsApproval={false}
            actionLabel="Save"
            onDiscard={() => reset(base)}
            onReview={save}
          />
        )}
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Save changes to ${name}?`}
        description="Every flag that uses this segment sees the change at once, in every environment, protected ones too."
        confirmLabel="Save changes"
        onConfirm={() => void send()}
      />
      {canWrite && (
        <SegmentDetailsDialog
          project={project}
          segment={current}
          open={editingDetails}
          onOpenChange={setEditingDetails}
          onSaved={(out) => setSaved(out)}
        />
      )}
    </>
  );
}
