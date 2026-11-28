"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { useRoles } from "@/components/access/use-catalogue";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { SecretReveal } from "@/components/patterns/secret-reveal";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import {
  ApiError,
  type ApiFailure,
  failureText,
  must,
} from "@/lib/flags/errors";

const NAME_MAX = 200;

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** The link of a new invitation, and why its role wasn't assigned, if it wasn't. */
type Invited = { url: string; expiresAt: string; roleFailure: string | null };

type Field = "email" | "name";
type FieldErrors = Partial<Record<Field, string>>;

function fieldErrors(email: string, name: string): FieldErrors {
  const errors: FieldErrors = {};
  if (email.trim() === "") errors.email = "Enter an email address";
  if (name.trim() === "") errors.name = "Enter a name";
  else if (name.length > NAME_MAX)
    errors.name = `Use at most ${NAME_MAX} characters`;
  return errors;
}

/** The field a failed create belongs to; null when it belongs to the whole form. */
function failureField(f: ApiFailure): Field | null {
  if (f.status === 409 && f.code === "key_conflict") return "email";
  if (f.status === 422) return f.path === "name" ? "name" : "email";
  return null;
}

/**
 * Invites a member: creates the user, gives them the organization role picked, and shows the
 * one-time link they set a password with. No email is sent.
 */
export function InviteDialog({
  open,
  onOpenChange,
  onInvited,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once the user exists, even when their role was refused. */
  onInvited: () => void;
}) {
  const [invited, setInvited] = useState<Invited | null>(null);
  return (
    <>
      <ResponsiveDialog
        open={open}
        onOpenChange={onOpenChange}
        title="Invite member"
        description="They get a link to set a password. Their role decides what they can do."
      >
        {open && (
          <Form
            onInvited={(result) => {
              onOpenChange(false);
              setInvited(result);
              onInvited();
            }}
          />
        )}
      </ResponsiveDialog>
      <SecretReveal
        secret={invited?.url ?? null}
        title="Invite link"
        description={
          invited &&
          `No email is sent. Share this link; it works once and expires ${DATE_TIME.format(new Date(invited.expiresAt))}.`
        }
        copyLabel="Copy link"
        onClose={() => setInvited(null)}
      >
        {invited?.roleFailure && (
          <Alert variant="destructive">
            <AlertDescription>
              The role was not assigned: {invited.roleFailure}
            </AlertDescription>
          </Alert>
        )}
      </SecretReveal>
    </>
  );
}

function Form({ onInvited }: { onInvited: (result: Invited) => void }) {
  const id = useId();
  const roles = useRoles();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  // Field errors show only after the first submit.
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const local = fieldErrors(email, name);
  const errors: FieldErrors = checked ? { ...server, ...local } : server;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    let created;
    try {
      created = await must(
        api.POST("/api/users", { body: { email: email.trim(), name } }),
      );
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        console.error("inviting the member failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const field = failureField(err.failure);
      if (field === null) setFailure(failureText(err.failure));
      else if (err.failure.code === "key_conflict")
        setServer({ email: "Someone with this email is already a member" });
      else setServer({ [field]: err.failure.message });
      return;
    }

    // The user exists now, so a refused role is a warning next to the link, not a failure.
    let roleFailure: string | null = null;
    if (role !== "") {
      try {
        await must(
          api.POST("/api/bindings", {
            body: {
              subject_kind: "user",
              subject_id: created.user.id,
              role,
              scope: "org",
            },
          }),
        );
      } catch (err) {
        if (err instanceof ApiError) roleFailure = formFailureText(err.failure);
        else {
          console.error("assigning the invited member's role failed", err);
          roleFailure = "Couldn't reach the server";
        }
      }
    }
    toast("Member invited");
    onInvited({
      url: created.invite.url,
      expiresAt: created.invite.expires_at,
      roleFailure,
    });
  };

  const field = (
    key: Field,
    label: string,
    input: React.ComponentProps<typeof Input>,
  ) => {
    const errorId = `${id}-${key}-error`;
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-${key}`}>{label}</Label>
        <Input
          id={`${id}-${key}`}
          {...input}
          {...(errors[key] && {
            "aria-invalid": true,
            "aria-describedby": errorId,
          })}
        />
        {errors[key] && (
          <p id={errorId} className="text-sm text-destructive">
            {errors[key]}
          </p>
        )}
      </div>
    );
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {field("email", "Email", {
        type: "email",
        autoComplete: "off",
        value: email,
        placeholder: "sam@example.com",
        onChange: (e) => {
          setEmail(e.target.value);
          setServer((s) => ({ ...s, email: undefined }));
        },
      })}
      {field("name", "Name", {
        autoComplete: "off",
        value: name,
        placeholder: "Sam Rivera",
        onChange: (e) => {
          setName(e.target.value);
          setServer((s) => ({ ...s, name: undefined }));
        },
      })}
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-role`}>Organization role</Label>
        <NativeSelect
          id={`${id}-role`}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="">No role</option>
          {(roles ?? []).map((r) => (
            <option key={r.key} value={r.key}>
              {r.name}
            </option>
          ))}
        </NativeSelect>
        <p className="text-sm text-muted-foreground">
          Applies in every project and environment.
        </p>
      </div>
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
          Invite
        </Button>
      </div>
    </form>
  );
}
