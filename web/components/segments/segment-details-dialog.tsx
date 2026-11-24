"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";

type SegmentOut = components["schemas"]["SegmentOut"];

const NAME_MAX = 200;

type Field = "name" | "description";
type FieldErrors = Partial<Record<Field, string>>;

function fieldErrors(name: string): FieldErrors {
  if (name.trim() === "") return { name: "Enter a name" };
  if (name.length > NAME_MAX)
    return { name: `Use at most ${NAME_MAX} characters` };
  return {};
}

/** Changes a segment's name and description; its keys and rules are saved on the page. */
export function SegmentDetailsDialog({
  project,
  segment,
  open,
  onOpenChange,
  onSaved,
}: {
  project: string;
  segment: SegmentOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (saved: SegmentOut) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Edit details"
      description="The key stays the same, so flag rules keep using the segment."
    >
      {open && (
        <Form
          project={project}
          segment={segment}
          onSaved={(saved) => {
            onSaved(saved);
            onOpenChange(false);
          }}
        />
      )}
    </ResponsiveDialog>
  );
}

function Form({
  project,
  segment,
  onSaved,
}: {
  project: string;
  segment: SegmentOut;
  onSaved: (saved: SegmentOut) => void;
}) {
  const id = useId();
  const [name, setName] = useState(segment.name);
  const [description, setDescription] = useState(segment.description);
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const local = fieldErrors(name);
  const errors: FieldErrors = checked ? { ...server, ...local } : server;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      const saved = await must(
        api.PATCH("/api/projects/{project}/segments/{segment}", {
          params: { path: { project, segment: segment.key } },
          body: { name, description },
        }),
      );
      toast("Segment saved");
      onSaved(saved);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("saving the segment details failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const path = err.failure.path;
      if (
        err.failure.status === 422 &&
        (path === "name" || path === "description")
      )
        setServer({ [path]: err.failure.message });
      else setFailure(formFailureText(err.failure));
    } finally {
      setBusy(false);
    }
  };

  const invalid = (which: Field) =>
    errors[which]
      ? { "aria-invalid": true, "aria-describedby": `${id}-${which}-error` }
      : {};

  const field = (which: Field, label: string, control: React.ReactNode) => (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={`${id}-${which}`}>{label}</Label>
      {control}
      {errors[which] && (
        <p id={`${id}-${which}-error`} className="text-sm text-destructive">
          {errors[which]}
        </p>
      )}
    </div>
  );

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {field(
        "name",
        "Name",
        <Input
          id={`${id}-name`}
          autoComplete="off"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setServer((s) => ({ ...s, name: undefined }));
          }}
          {...invalid("name")}
        />,
      )}
      {field(
        "description",
        "Description",
        <Textarea
          id={`${id}-description`}
          className="md:text-base"
          value={description}
          onChange={(e) => {
            setDescription(e.target.value);
            setServer((s) => ({ ...s, description: undefined }));
          }}
          {...invalid("description")}
        />,
      )}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          Save
        </Button>
      </div>
    </form>
  );
}
