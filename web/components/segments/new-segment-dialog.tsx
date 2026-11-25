"use client";

import { useRouter } from "next/navigation";
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
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { KEY_PATTERN, slugifyKey } from "@/lib/flags/flag-form";
import { segmentPath } from "@/lib/routes";

const NAME_MAX = 200;
const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

type Field = "name" | "key" | "description";
type FieldErrors = Partial<Record<Field, string>>;

function fieldErrors(name: string, key: string): FieldErrors {
  const errors: FieldErrors = {};
  if (name.trim() === "") errors.name = "Enter a name";
  else if (name.length > NAME_MAX)
    errors.name = `Use at most ${NAME_MAX} characters`;
  if (!KEY_PATTERN.test(key)) errors.key = KEY_HINT;
  return errors;
}

/** The field a failed create belongs to; null when it belongs to the whole form. */
function failureField(f: ApiFailure): Field | null {
  if (f.status === 409 && f.code === "key_conflict") return "key";
  if (f.status === 422)
    return f.path === "name" || f.path === "description" ? f.path : "key";
  return null;
}

/** Creates a segment in the project, then opens its page. */
export function NewSegmentDialog({
  project,
  open,
  onOpenChange,
}: {
  project: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New segment"
      description="A named group of context keys and rules that flag rules can target. You add its keys and rules next."
    >
      {open && <Form project={project} />}
    </ResponsiveDialog>
  );
}

function Form({ project }: { project: string }) {
  const id = useId();
  const router = useRouter();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState("");
  // Field errors show only after the first submit.
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const local = fieldErrors(name, key);
  const errors: FieldErrors = checked ? { ...server, ...local } : server;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      const created = await must(
        api.POST("/api/projects/{project}/segments", {
          params: { path: { project } },
          body: { key, name, description },
        }),
      );
      toast("Segment created");
      // Stays busy: the dialog goes away with the page.
      router.push(segmentPath(project, created.key));
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        console.error("creating the segment failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const field = failureField(err.failure);
      if (field === null) setFailure(formFailureText(err.failure));
      else if (err.failure.code === "key_conflict")
        setServer({ key: "Another segment already uses this key" });
      else setServer({ [field]: err.failure.message });
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
          placeholder="Beta testers"
          onChange={(e) => {
            setName(e.target.value);
            if (!keyEdited) setKey(slugifyKey(e.target.value));
            setServer((s) => ({ ...s, name: undefined }));
          }}
          {...invalid("name")}
        />,
      )}
      {field(
        "key",
        "Key",
        <Input
          id={`${id}-key`}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          className="font-mono"
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            setKeyEdited(true);
            setServer((s) => ({ ...s, key: undefined }));
          }}
          {...invalid("key")}
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
          Create segment
        </Button>
      </div>
    </form>
  );
}
