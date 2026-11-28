"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { KEY_PATTERN, slugifyKey } from "@/lib/flags/flag-form";

type Environment = components["schemas"]["EnvironmentOut"];

const NAME_MAX = 200;
const MAX_APPROVALS = 10;
const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

type Field = "name" | "key" | "description" | "approvals";
type FieldErrors = Partial<Record<Field, string>>;

/** The approvals typed, or null when it is not a whole number from 1 to 10. */
function approvalsCount(text: string): number | null {
  if (!/^\d+$/.test(text.trim())) return null;
  const n = Number(text);
  return n >= 1 && n <= MAX_APPROVALS ? n : null;
}

/** The field a failed save belongs to; null when it belongs to the whole form. */
function failureField(f: ApiFailure): Field | null {
  if (f.status === 409 && f.code === "key_conflict") return "key";
  if (f.status !== 422) return null;
  if (f.path === "name" || f.path === "description" || f.path === "key")
    return f.path;
  return f.path === "approvals_required" ? "approvals" : null;
}

/** Creates an environment (`environment` null) or edits one, including the approvals it needs. */
export function EnvironmentDialog({
  project,
  environment,
  open,
  onOpenChange,
  onSaved,
}: {
  project: string;
  environment: Environment | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (saved: Environment) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={environment ? "Edit environment" : "New environment"}
      description={
        environment
          ? "The key stays the same, so SDKs and links keep working."
          : "Flags are turned on and targeted separately in each environment."
      }
    >
      {open && (
        <Form
          project={project}
          environment={environment}
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
  environment,
  onSaved,
}: {
  project: string;
  environment: Environment | null;
  onSaved: (saved: Environment) => void;
}) {
  const id = useId();
  const creating = environment === null;
  const hadApprovals = (environment?.approvals_required ?? 0) > 0;
  const [name, setName] = useState(environment?.name ?? "");
  const [key, setKey] = useState(environment?.key ?? "");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState(
    environment?.description ?? "",
  );
  const [protect, setProtect] = useState(hadApprovals);
  const [approvals, setApprovals] = useState(
    String(hadApprovals ? environment?.approvals_required : 1),
  );
  // Name and key errors show only after the first submit; the approvals range shows as typed.
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const count = approvalsCount(approvals);
  const approvalsError =
    protect && count === null ? `Enter 1 to ${MAX_APPROVALS}` : undefined;
  const local: FieldErrors = {};
  if (name.trim() === "") local.name = "Enter a name";
  else if (name.length > NAME_MAX)
    local.name = `Use at most ${NAME_MAX} characters`;
  if (creating && !KEY_PATTERN.test(key)) local.key = KEY_HINT;
  const errors: FieldErrors = {
    ...server,
    ...(checked ? local : {}),
    ...(approvalsError ? { approvals: approvalsError } : {}),
  };

  const save = (): Promise<Environment> =>
    environment
      ? must(
          api.PATCH("/api/projects/{project}/environments/{env}", {
            params: { path: { project, env: environment.key } },
            body: {
              name,
              description,
              approvals_required: protect ? (count ?? 0) : 0,
            },
          }),
        )
      : must(
          api.POST("/api/projects/{project}/environments", {
            params: { path: { project } },
            body: { key, name, description },
          }),
        );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0 || approvalsError) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      const saved = await save();
      toast(creating ? "Environment created" : "Environment saved");
      onSaved(saved);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("saving the environment failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const field = failureField(err.failure);
      if (field === null) setFailure(formFailureText(err.failure));
      else setServer({ [field]: err.failure.message });
    } finally {
      setBusy(false);
    }
  };

  const invalid = (which: Field) =>
    errors[which]
      ? { "aria-invalid": true, "aria-describedby": `${id}-${which}-error` }
      : {};

  const error = (which: Field) =>
    errors[which] && (
      <p id={`${id}-${which}-error`} className="text-sm text-destructive">
        {errors[which]}
      </p>
    );

  const field = (which: Field, label: string, control: React.ReactNode) => (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={`${id}-${which}`}>{label}</Label>
      {control}
      {error(which)}
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
          placeholder="QA"
          onChange={(e) => {
            setName(e.target.value);
            if (creating && !keyEdited) setKey(slugifyKey(e.target.value));
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
          readOnly={!creating}
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
      {creating ? (
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
          <li>Every flag gets this environment, turned off.</li>
          <li>
            It&apos;s added last. You can move it, and require approvals for
            changes, once it exists.
          </li>
        </ul>
      ) : (
        <div className="flex min-w-0 flex-col gap-2">
          <label
            data-hit-area
            className="inline-flex min-h-11 min-w-11 items-center gap-2"
          >
            <Checkbox
              checked={protect}
              aria-describedby={`${id}-protect-hint`}
              onChange={(e) => {
                setProtect(e.target.checked);
                setServer((s) => ({ ...s, approvals: undefined }));
              }}
            />
            Changes need approval
          </label>
          <p
            id={`${id}-protect-hint`}
            className="text-sm text-muted-foreground"
          >
            Changes here go through a change request that others approve.
          </p>
          {protect &&
            field(
              "approvals",
              "Approvals needed",
              <Input
                id={`${id}-approvals`}
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_APPROVALS}
                step={1}
                className="w-24"
                value={approvals}
                onChange={(e) => {
                  setApprovals(e.target.value);
                  setServer((s) => ({ ...s, approvals: undefined }));
                }}
                {...invalid("approvals")}
              />,
            )}
          {hadApprovals && !protect && (
            <p className="text-sm text-muted-foreground">
              Open change requests stay open and still need one approval.
            </p>
          )}
        </div>
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
        <Button type="submit" disabled={busy || approvalsError !== undefined}>
          {creating ? "Create environment" : "Save"}
        </Button>
      </div>
    </form>
  );
}
