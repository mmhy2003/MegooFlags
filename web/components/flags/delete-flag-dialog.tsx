"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api/client";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { projectPath } from "@/lib/routes";

/** Deletes an archived flag once its key is typed, then goes back to the flag list. */
export function DeleteFlagDialog({
  project,
  flag,
}: {
  project: string;
  flag: string;
}) {
  const router = useRouter();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [sending, setSending] = useState(false);

  const remove = async () => {
    setSending(true);
    try {
      await must(
        api.DELETE("/api/projects/{project}/flags/{flag}", {
          params: { path: { project, flag } },
        }),
      );
      toast("Flag deleted");
      setOpen(false);
      router.push(projectPath(project));
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("delete failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
      setSending(false);
    }
  };

  return (
    <ResponsiveDialog
      alert
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setTyped("");
      }}
      trigger={<Button variant="destructive">Delete flag</Button>}
      title={`Delete ${flag}?`}
      description="This removes the flag and its targeting in every environment. It can't be undone."
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button
            variant="destructive"
            disabled={sending || typed !== flag}
            onClick={remove}
          >
            Delete
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-confirm`}>Type {flag} to confirm</Label>
        <Input
          id={`${id}-confirm`}
          value={typed}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
    </ResponsiveDialog>
  );
}
