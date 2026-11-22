"use client";

import Link from "next/link";
import { notFound } from "next/navigation";
import { useId, useState } from "react";

import { Comments } from "@/components/change-requests/comments";
import { ProposedChange } from "@/components/change-requests/proposed-change";
import { ReviewActions } from "@/components/change-requests/review-actions";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { PageHeader } from "@/components/patterns/page-header";
import { StatusPill } from "@/components/patterns/status-pill";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useApiData } from "@/hooks/use-api-data";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { canComment } from "@/lib/change-requests/filters";
import {
  approvalsText,
  crPill,
  isStale,
  KIND_LABELS,
} from "@/lib/change-requests/status";
import { must } from "@/lib/flags/errors";
import { flagPath, projectPath, projectTrail, type Crumb } from "@/lib/routes";

type S = components["schemas"];

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

const DECISIONS: Record<S["ReviewDecision"], string> = {
  approve: "Approved",
  reject: "Rejected",
};

function trail(project: string): Crumb[] {
  return [
    ...projectTrail(project),
    {
      label: "Change requests",
      href: projectPath(project, "change-requests"),
    },
  ];
}

function Reviews({ reviews }: { reviews: S["ReviewOut"][] }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="text-lg font-semibold">
        Reviews
      </h2>
      {reviews.length === 0 ? (
        <p className="text-muted-foreground">No reviews yet.</p>
      ) : (
        <ul aria-label="Reviews" className="flex flex-col gap-3">
          {reviews.map((r) => (
            <li
              key={`${r.reviewer}-${r.created_at}`}
              className="flex min-w-0 flex-col gap-1 rounded-lg border p-3"
            >
              <div className="flex flex-wrap gap-x-2 text-sm">
                <span className="font-medium">{DECISIONS[r.decision]}</span>
                <span className="break-all text-muted-foreground">
                  {r.reviewer}
                </span>
                <time dateTime={r.created_at} className="text-muted-foreground">
                  {DATE_TIME.format(new Date(r.created_at))}
                </time>
              </div>
              {r.comment && (
                <p className="break-words whitespace-pre-wrap">{r.comment}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * A change request: what it changes and where, a warning when the flag changed since, its
 * reviews, its comments and the review actions. An unknown or hidden request is the not-found
 * page.
 */
export function ChangeRequestDetail({
  project,
  id,
}: {
  project: string;
  id: string;
}) {
  const changeId = useId();
  // The request as the last action returned it, until the next load.
  const [changed, setChanged] = useState<S["ChangeRequestOut"] | null>(null);
  const cr = useApiData(`change-request:${project}:${id}`, () =>
    must(
      api.GET("/api/projects/{project}/change-requests/{id}", {
        params: { path: { project, id } },
      }),
    ),
  );
  // Config requests carry the live config; the others load it for the summary and stale check.
  const needsConfig = cr.data !== null && cr.data.kind !== "config";
  const config = useApiData(
    needsConfig && cr.data
      ? `config:${project}:${cr.data.flag}:${cr.data.environment}`
      : null,
    () =>
      must(
        api.GET("/api/projects/{project}/flags/{flag}/environments/{env}", {
          params: {
            path: {
              project,
              flag: cr.data?.flag ?? "",
              env: cr.data?.environment ?? "",
            },
          },
        }),
      ),
  );
  const me = useApiData("me", () => must(api.GET("/api/auth/me")));
  const perms = useProjectPermissions(project, null, null);

  if (cr.failure?.status === 404) notFound();
  if (cr.failure)
    return (
      <>
        <PageHeader title="Change request" trail={trail(project)} />
        <LoadFailed
          title="Couldn't load the change request"
          failure={cr.failure}
          onRetry={cr.reload}
        />
      </>
    );
  if (!cr.data)
    return (
      <>
        <PageHeader title="Change request" trail={trail(project)} />
        <LoadingRows label="Loading the change request" />
      </>
    );

  const request = changed?.id === cr.data.id ? changed : cr.data;
  const version =
    request.kind === "config"
      ? (request.current?.version ?? null)
      : (config.data?.version ?? null);
  const isAuthor = me.data?.label === request.author;
  const showCommentBox =
    request.status === "open" &&
    canComment(perms, request.environment, isAuthor);

  return (
    <>
      <PageHeader
        title={request.title}
        trail={trail(project)}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusPill {...crPill(request.status)} />
            <span>{approvalsText(request)}</span>
          </span>
        }
      />
      <div className="flex min-w-0 flex-col gap-6">
        {request.status === "failed" && request.error && (
          <Alert variant="destructive">
            <AlertTitle>The change couldn&apos;t be applied</AlertTitle>
            <AlertDescription>{request.error}</AlertDescription>
          </Alert>
        )}
        {isStale(request, version) && (
          <Alert>
            <AlertTitle>
              The flag changed after this request was made. Approving it will
              fail.
            </AlertTitle>
          </Alert>
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-4 gap-y-2">
          <dt className="text-muted-foreground">Flag</dt>
          <dd className="min-w-0">
            <Link
              href={`${flagPath(project, request.flag)}?env=${encodeURIComponent(request.environment)}`}
              className="inline-flex min-h-11 items-center font-medium break-all hover:underline"
            >
              {request.flag}
            </Link>
          </dd>
          <dt className="text-muted-foreground">Environment</dt>
          <dd className="break-all">{request.environment}</dd>
          <dt className="text-muted-foreground">Change</dt>
          <dd>{KIND_LABELS[request.kind]}</dd>
          <dt className="text-muted-foreground">Author</dt>
          <dd className="break-all">{request.author}</dd>
          <dt className="text-muted-foreground">Created</dt>
          <dd>
            <time dateTime={request.created_at}>
              {DATE_TIME.format(new Date(request.created_at))}
            </time>
          </dd>
        </dl>
        {request.description && (
          <p className="break-words whitespace-pre-wrap">
            {request.description}
          </p>
        )}
        <section aria-labelledby={changeId} className="flex flex-col gap-3">
          <h2 id={changeId} className="text-lg font-semibold">
            Proposed change
          </h2>
          <ProposedChange project={project} cr={request} config={config.data} />
        </section>
        <Reviews reviews={request.reviews} />
        <Comments
          project={project}
          id={request.id}
          comments={request.comments}
          canComment={showCommentBox}
        />
      </div>
      <ReviewActions
        project={project}
        cr={request}
        onChanged={setChanged}
        onReload={() => {
          setChanged(null);
          cr.reload();
        }}
      />
    </>
  );
}
