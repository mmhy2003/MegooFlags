"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { projectPath } from "@/lib/routes";

/** What the dialog asks for: turning `flag` on or off in an environment that needs approval. */
export type ToggleRequest = {
  env: string;
  envName: string;
  enabled: boolean;
  version: number;
};

function RequestForm({
  project,
  flag,
  request,
  onDone,
}: {
  project: string;
  flag: string;
  request: ToggleRequest;
  onDone: () => void;
}) {
  const id = useId();
  const [title, setTitle] = useState(
    `Turn ${request.enabled ? "on" : "off"} ${flag} in ${request.envName}`,
  );
  const [description, setDescription] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    try {
      await must(
        api.POST("/api/projects/{project}/change-requests", {
          params: { path: { project } },
          body: {
            environment: request.env,
            flag,
            kind: "toggle",
            title: title.trim(),
            description,
            payload: { enabled: request.enabled, version: request.version },
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
      onDone();
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("request failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
      setSending(false);
    }
  };

  return (
    <form id={`${id}-form`} onSubmit={submit} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-title`}>Title</Label>
        <Input
          id={`${id}-title`}
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-description`}>Description</Label>
        <Textarea
          id={`${id}-description`}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={sending || title.trim() === ""}>
          Request change
        </Button>
      </div>
    </form>
  );
}

/**
 * Asks for a title and description, then opens a `toggle` change request; the flag's state is
 * left as it is until the request is approved.
 */
export function ToggleRequestDialog({
  project,
  flag,
  request,
  onClose,
}: {
  project: string;
  flag: string;
  request: ToggleRequest | null;
  onClose: () => void;
}) {
  return (
    <ResponsiveDialog
      open={request !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Request change in ${request?.envName ?? ""}`}
      description="This environment needs approval, so the change waits for a reviewer."
    >
      {request && (
        <RequestForm
          key={`${request.env}:${request.enabled}`}
          project={project}
          flag={flag}
          request={request}
          onDone={onClose}
        />
      )}
    </ResponsiveDialog>
  );
}
