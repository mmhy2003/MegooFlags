"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";

import { DiffViewer } from "@/components/patterns/diff-viewer";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  type ConfigDraft,
  configText,
  toConfigIn,
  withState,
} from "@/lib/flags/config-draft";
import {
  ApiError,
  type ApiFailure,
  failureText,
  must,
} from "@/lib/flags/errors";
import { projectPath } from "@/lib/routes";

type FlagConfigOut = components["schemas"]["FlagConfigOut"];

type ReviewProps = {
  project: string;
  flag: string;
  env: string;
  envName: string;
  /** The environment needs approval: the draft becomes a change request. */
  needsApproval: boolean;
  /** The saved config the draft is compared with; the latest one after a conflict. */
  base: ConfigDraft;
  draft: ConfigDraft;
  /** The draft was saved. */
  onSaved: (saved: FlagConfigOut) => void;
  /** Someone saved a newer version; it becomes the base. */
  onConflict: (latest: FlagConfigOut) => void;
  /** A change request was created for the draft. */
  onRequested: () => void;
  /** A 422; true when the editor shows it at a field, which closes the dialog. */
  onInvalid: (failure: ApiFailure) => boolean;
};

function ReviewForm({
  project,
  flag,
  env,
  envName,
  needsApproval,
  base,
  draft,
  onSaved,
  onConflict,
  onRequested,
  onInvalid,
}: ReviewProps) {
  const id = useId();
  const [mode, setMode] = useState<"save" | "request">(
    needsApproval ? "request" : "save",
  );
  const [conflict, setConflict] = useState(false);
  const [alert, setAlert] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState(
    `Update targeting of ${flag} in ${envName}`,
  );
  const [description, setDescription] = useState("");
  const path = { path: { project, flag, env } };

  const failed = async (error: unknown) => {
    if (!(error instanceof ApiError)) {
      console.error("review failed", error);
      setAlert("Couldn't reach the server");
      return;
    }
    const { failure } = error;
    if (failure.code === "version_conflict") {
      try {
        const latest = await must(
          api.GET("/api/projects/{project}/flags/{flag}/environments/{env}", {
            params: path,
          }),
        );
        onConflict(latest);
        setConflict(true);
      } catch (reload) {
        if (!(reload instanceof ApiError))
          console.error("reload failed", reload);
        setAlert(
          reload instanceof ApiError
            ? failureText(reload.failure)
            : "Couldn't reach the server",
        );
      }
      return;
    }
    if (failure.code === "approval_required") {
      setMode("request");
      return;
    }
    if (failure.status === 422 && onInvalid(failure)) return;
    setAlert(failureText(failure));
  };

  const run = async (call: () => Promise<void>) => {
    setBusy(true);
    setAlert(null);
    try {
      await call();
    } catch (error) {
      await failed(error);
    } finally {
      setBusy(false);
    }
  };

  // After a conflict the draft goes over the latest version, keeping its on/off state.
  const outgoing = () => (conflict ? withState(draft, base) : draft);

  const save = () =>
    run(async () => {
      const saved = await must(
        api.PUT("/api/projects/{project}/flags/{flag}/environments/{env}", {
          params: path,
          body: toConfigIn(outgoing()),
        }),
      );
      toast("Saved");
      onSaved(saved);
    });

  const request = () =>
    run(async () => {
      await must(
        api.POST("/api/projects/{project}/change-requests", {
          params: { path: { project } },
          body: {
            environment: env,
            flag,
            kind: "config",
            title: title.trim(),
            description,
            payload: toConfigIn(outgoing()),
          },
        }),
      );
      toast("Change request created", {
        description: (
          <Link
            href={projectPath(project, "change-requests")}
            className="underline"
          >
            View change requests
          </Link>
        ),
      });
      onRequested();
    });

  return (
    <>
      {conflict && (
        <Alert variant="destructive">
          <AlertTitle>Someone saved a newer version</AlertTitle>
          <AlertDescription>
            Your changes are kept. Before is the latest version; check it, then
            save over it.
          </AlertDescription>
        </Alert>
      )}
      {alert && (
        <Alert variant="destructive">
          <AlertTitle>{alert}</AlertTitle>
        </Alert>
      )}
      <DiffViewer
        label="Changes"
        before={configText(base)}
        after={configText(draft)}
      />
      {mode === "request" && (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-title`}>Title</Label>
            <Input
              id={`${id}-title`}
              className="text-base"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-description`}>Description</Label>
            <Textarea
              id={`${id}-description`}
              className="text-base"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose
          render={
            <Button type="button" variant="outline" className="min-h-11" />
          }
        >
          Cancel
        </DialogClose>
        {mode === "request" ? (
          <Button
            className="min-h-11"
            disabled={busy || title.trim() === ""}
            onClick={() => void request()}
          >
            Request change
          </Button>
        ) : (
          <Button
            className="min-h-11"
            disabled={busy}
            onClick={() => void save()}
          >
            {conflict ? "Save over latest" : "Save"}
          </Button>
        )}
      </div>
    </>
  );
}

/**
 * "Review changes": the draft against the saved config, then Save, or Request change in an
 * environment that needs approval. A newer saved version is shown beside the draft, which is
 * never dropped, and can be saved over.
 */
export function ReviewDialog({
  open,
  onOpenChange,
  ...props
}: ReviewProps & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Review changes"
      description={`Targeting of ${props.flag} in ${props.envName}.`}
    >
      {open && <ReviewForm {...props} />}
    </ResponsiveDialog>
  );
}
