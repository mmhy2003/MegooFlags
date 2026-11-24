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
import { projectPath } from "@/lib/routes";

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

/** Creates a project with the default environments, then opens it. */
export function NewProjectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="New project"
      description="A project holds flags, segments and environments. It starts with Development, Staging and Production."
    >
      {open && <Form onDone={() => onOpenChange(false)} />}
    </ResponsiveDialog>
  );
}

function Form({ onDone }: { onDone: () => void }) {
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
      await must(
        api.POST("/api/projects", {
          body: { key, name: name.trim(), description },
        }),
      );
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        console.error("creating the project failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const field = failureField(err.failure);
      if (field === null) setFailure(formFailureText(err.failure));
      else if (err.failure.code === "key_conflict")
        setServer({ key: "Another project already uses this key" });
      else setServer({ [field]: err.failure.message });
      return;
    }

    // project.create alone doesn't let the creator read the project. When the check can't
    // reach the server, the project page itself says what went wrong.
    const read = await api
      .GET("/api/projects/{project}", { params: { path: { project: key } } })
      .catch(() => null);
    if (read?.response.status === 404) {
      toast(
        "Project created, but you have no access to it. Ask an admin for a role in it.",
      );
      onDone();
      router.refresh();
      return;
    }
    toast("Project created");
    // The switcher's dialog lives in the shell, which stays across the navigation.
    onDone();
    router.push(projectPath(key));
    router.refresh();
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
          placeholder="Checkout"
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
          Create project
        </Button>
      </div>
    </form>
  );
}
