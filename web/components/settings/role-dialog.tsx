"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { usePermissionCatalogue } from "@/components/access/use-catalogue";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { LoadingRows } from "@/components/patterns/list-state";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { formFailureText } from "@/lib/access/bindings";
import {
  groupByLevel,
  LEVEL_TEXT,
  PERMISSION_LABELS,
  type Permission,
} from "@/lib/access/permissions";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { KEY_PATTERN, slugifyKey } from "@/lib/flags/flag-form";

type Role = components["schemas"]["RoleOut"];

const NAME_MAX = 200;
const DESCRIPTION_MAX = 10000;
const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

/**
 * A new role (`role` null) or one role. Built-in roles, and every role without `canManage`,
 * only show their permissions.
 */
export function RoleDialog({
  role,
  open,
  onOpenChange,
  canManage,
  onChanged,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  role: Role | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
  /** Called after each change, so the list shows it. */
  onChanged: () => void;
}) {
  // A rename shows in the title before the list reloads.
  const [names, setNames] = useState<Record<string, string>>({});
  const title = role ? (names[role.id] ?? role.name) : "New role";
  const readOnly = role !== null && (role.builtin || !canManage);
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={
        role
          ? role.key
          : "Pick what people with this role can do. You grant it to people, teams and tokens."
      }
    >
      {open &&
        (role && readOnly ? (
          <View role={role} />
        ) : (
          <RoleForm
            key={role?.id ?? "new"}
            role={role}
            onSaved={(saved) => {
              if (role) {
                setNames((n) => ({ ...n, [role.id]: saved.name }));
              } else {
                onOpenChange(false);
              }
              onChanged();
            }}
            onDeleted={() => {
              onOpenChange(false);
              onChanged();
            }}
          />
        ))}
    </ResponsiveDialog>
  );
}

/** The role's permissions as text, under the level each works at. */
function View({ role }: { role: Role }) {
  const id = useId();
  const catalogue = usePermissionCatalogue();
  if (!catalogue) return <LoadingRows label="Loading the permissions" />;
  const groups = groupByLevel(catalogue)
    .map((g) => ({
      ...g,
      permissions: g.permissions.filter((p) => role.permissions.includes(p)),
    }))
    .filter((g) => g.permissions.length > 0);
  return (
    <div className="flex flex-col gap-5">
      {role.description && (
        <p className="break-words whitespace-pre-line">{role.description}</p>
      )}
      {groups.map((g) => (
        <section
          key={g.level}
          aria-labelledby={`${id}-${g.level}`}
          className="flex flex-col gap-2"
        >
          <h3 id={`${id}-${g.level}`} className="font-medium">
            {LEVEL_TEXT[g.level].label}
          </h3>
          <p className="text-sm text-muted-foreground">
            {LEVEL_TEXT[g.level].where}
          </p>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {g.permissions.map((p) => (
              <li key={p}>{PERMISSION_LABELS[p]}</li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

type Field = "name" | "key" | "description" | "permissions";
type FieldErrors = Partial<Record<Field, string>>;

function fieldErrors(
  name: string,
  key: string | null,
  description: string,
  permissions: Permission[],
): FieldErrors {
  const errors: FieldErrors = {};
  if (name.trim() === "") errors.name = "Enter a name";
  else if (name.length > NAME_MAX)
    errors.name = `Use at most ${NAME_MAX} characters`;
  if (key !== null && !KEY_PATTERN.test(key)) errors.key = KEY_HINT;
  if (description.length > DESCRIPTION_MAX)
    errors.description = `Use at most ${DESCRIPTION_MAX} characters`;
  if (permissions.length === 0)
    errors.permissions = "Pick at least one permission";
  return errors;
}

/** The field a failed save belongs to; null when it belongs to the whole form. */
function failureField(f: ApiFailure): Field | null {
  if (f.status === 409 && f.code === "key_conflict") return "key";
  if (f.status === 422) {
    const field = f.path?.split(".")[0];
    if (
      field === "name" ||
      field === "key" ||
      field === "description" ||
      field === "permissions"
    )
      return field;
  }
  return null;
}

/** Creates a role (`role` null) or edits a custom one, which it can also delete. */
function RoleForm({
  role,
  onSaved,
  onDeleted,
}: {
  role: Role | null;
  onSaved: (role: Role) => void;
  onDeleted: () => void;
}) {
  const id = useId();
  const catalogue = usePermissionCatalogue();
  const [name, setName] = useState(role?.name ?? "");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState(role?.description ?? "");
  const [permissions, setPermissions] = useState<Permission[]>(
    role?.permissions ?? [],
  );
  // Field errors show only after the first submit.
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const local = fieldErrors(name, role ? null : key, description, permissions);
  const errors: FieldErrors = checked ? { ...server, ...local } : server;

  const showFailure = (err: unknown, what: string) => {
    if (!(err instanceof ApiError)) {
      console.error(what, err);
      setFailure("Couldn't reach the server");
      return;
    }
    const field = failureField(err.failure);
    if (field === null) setFailure(formFailureText(err.failure));
    else if (err.failure.code === "key_conflict")
      setServer({ key: "Another role already uses this key" });
    else setServer({ [field]: err.failure.message });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      const saved = role
        ? await must(
            api.PATCH("/api/roles/{key}", {
              params: { path: { key: role.key } },
              body: { name, description, permissions },
            }),
          )
        : await must(
            api.POST("/api/roles", {
              body: { key, name, description, permissions },
            }),
          );
      toast(role ? "Role saved" : "Role created");
      onSaved(saved);
    } catch (err) {
      showFailure(
        err,
        role ? "saving the role failed" : "creating the role failed",
      );
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!role) return;
    setBusy(true);
    setFailure(null);
    try {
      await must(
        api.DELETE("/api/roles/{key}", { params: { path: { key: role.key } } }),
      );
      toast("Role deleted");
      onDeleted();
    } catch (err) {
      showFailure(err, "deleting the role failed");
    } finally {
      setBusy(false);
    }
  };

  const errorProps = (which: Field) =>
    errors[which] && {
      "aria-invalid": true,
      "aria-describedby": `${id}-${which}-error`,
    };
  const errorText = (which: Field) =>
    errors[which] && (
      <p id={`${id}-${which}-error`} className="text-sm text-destructive">
        {errors[which]}
      </p>
    );

  const toggle = (p: Permission, on: boolean) => {
    setPermissions((held) =>
      on ? [...held, p] : held.filter((other) => other !== p),
    );
    setServer((s) => ({ ...s, permissions: undefined }));
  };

  return (
    <div className="flex flex-col gap-5">
      <form noValidate onSubmit={submit} className="flex flex-col gap-5">
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={`${id}-name`}>Name</Label>
          <Input
            id={`${id}-name`}
            autoComplete="off"
            placeholder="Release manager"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!role && !keyEdited) setKey(slugifyKey(e.target.value));
              setServer((s) => ({ ...s, name: undefined }));
            }}
            {...errorProps("name")}
          />
          {errorText("name")}
        </div>
        {!role && (
          <div className="flex min-w-0 flex-col gap-2">
            <Label htmlFor={`${id}-key`}>Key</Label>
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
              {...errorProps("key")}
            />
            {errorText("key")}
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={`${id}-description`}>Description</Label>
          <Textarea
            id={`${id}-description`}
            value={description}
            onChange={(e) => {
              setDescription(e.target.value);
              setServer((s) => ({ ...s, description: undefined }));
            }}
            {...errorProps("description")}
          />
          {errorText("description")}
        </div>

        {catalogue ? (
          <div className="flex flex-col gap-4">
            {groupByLevel(catalogue).map((g) => (
              <fieldset
                key={g.level}
                aria-describedby={`${id}-${g.level}-where`}
                className="flex flex-col gap-1"
              >
                <legend className="font-medium">
                  {LEVEL_TEXT[g.level].label}
                </legend>
                <p
                  id={`${id}-${g.level}-where`}
                  className="mb-1 text-sm text-muted-foreground"
                >
                  {LEVEL_TEXT[g.level].where}
                </p>
                {g.permissions.map((p) => (
                  <label
                    key={p}
                    data-hit-area
                    className="inline-flex min-h-11 min-w-11 items-center gap-2"
                  >
                    <Checkbox
                      checked={permissions.includes(p)}
                      onChange={(e) => toggle(p, e.target.checked)}
                    />
                    {PERMISSION_LABELS[p]}
                  </label>
                ))}
              </fieldset>
            ))}
            {errorText("permissions")}
          </div>
        ) : (
          <LoadingRows label="Loading the permissions" />
        )}

        {failure && (
          <p role="alert" className="text-sm break-words text-destructive">
            {failure}
          </p>
        )}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <DialogClose render={<Button type="button" variant="outline" />}>
            Cancel
          </DialogClose>
          <Button type="submit" disabled={busy}>
            {role ? "Save" : "Create role"}
          </Button>
        </div>
      </form>

      {role && (
        <>
          <Separator />
          <div>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => setDeleting(true)}
            >
              Delete role
            </Button>
          </div>
          <ConfirmDialog
            open={deleting}
            onOpenChange={setDeleting}
            title={`Delete ${role.name}?`}
            description="You can't delete a role while people, teams, tokens or SSO mappings use it."
            confirmLabel="Delete role"
            destructive
            onConfirm={() => void remove()}
          />
        </>
      )}
    </div>
  );
}
