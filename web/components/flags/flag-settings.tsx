"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { DeleteFlagDialog } from "@/components/flags/delete-flag-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { StatusPill } from "@/components/patterns/status-pill";
import { Toggle } from "@/components/patterns/toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  formatVariationValue,
  KEY_PATTERN,
  parseTags,
  parseVariationValue,
} from "@/lib/flags/flag-form";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

type S = components["schemas"];
type FlagOut = S["FlagOut"];
type VariationOut = S["VariationOut"];

/** Runs an API call; a failure is a toast, a success the `done` toast. Returns whether it worked. */
async function attempt(
  call: () => Promise<unknown>,
  done: string | null = "Saved",
): Promise<boolean> {
  try {
    await call();
    if (done) toast(done);
    return true;
  } catch (error) {
    if (!(error instanceof ApiError)) console.error("settings failed", error);
    toast.error(
      error instanceof ApiError
        ? failureText(error.failure)
        : "Couldn't reach the server",
    );
    return false;
  }
}

function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p id={`${id}-error`} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function invalid(id: string, error: string | undefined) {
  return error
    ? { "aria-invalid": true, "aria-describedby": `${id}-error` }
    : {};
}

function ReadOnlyRow({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="text-sm text-muted-foreground">{term}</dt>
      <dd className="break-words">{value || "—"}</dd>
    </div>
  );
}

function DetailsCard({
  project,
  flag,
  canEdit,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const id = useId();
  const [name, setName] = useState(flag.name);
  const [description, setDescription] = useState(flag.description);
  const [tags, setTags] = useState(flag.tags.join(", "));
  const [owner, setOwner] = useState(flag.owner ?? "");
  const [saving, setSaving] = useState(false);

  const patch: S["FlagUpdate"] = {};
  if (name.trim() !== flag.name) patch.name = name.trim();
  if (description !== flag.description) patch.description = description;
  const parsedTags = parseTags(tags);
  if (parsedTags.join("\n") !== flag.tags.join("\n")) patch.tags = parsedTags;
  if ((owner.trim() || null) !== flag.owner) patch.owner = owner.trim() || null;
  const nameError = name.trim() ? undefined : "Enter a name";

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    const saved = await attempt(() =>
      must(
        api.PATCH("/api/projects/{project}/flags/{flag}", {
          params: { path: { project, flag: flag.key } },
          body: patch,
        }),
      ),
    );
    setSaving(false);
    if (saved) onChanged();
  };

  return (
    <Card className="min-w-0 px-4">
      <CardHeader className="px-0">
        <CardTitle>
          <h2>Details</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        {canEdit ? (
          <form onSubmit={save} className="flex flex-col gap-4">
            <Field id={`${id}-name`} label="Name" error={nameError}>
              <Input
                id={`${id}-name`}
                value={name}
                maxLength={200}
                onChange={(e) => setName(e.target.value)}
                {...invalid(`${id}-name`, nameError)}
              />
            </Field>
            <Field id={`${id}-description`} label="Description">
              <Textarea
                id={`${id}-description`}
                value={description}
                className="text-base"
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
            <Field id={`${id}-tags`} label="Tags">
              <Input
                id={`${id}-tags`}
                value={tags}
                placeholder="web, checkout"
                onChange={(e) => setTags(e.target.value)}
              />
            </Field>
            <Field id={`${id}-owner`} label="Owner">
              <Input
                id={`${id}-owner`}
                value={owner}
                maxLength={200}
                onChange={(e) => setOwner(e.target.value)}
              />
            </Field>
            <div>
              <Button
                type="submit"
                className="min-h-11"
                disabled={
                  saving || !!nameError || Object.keys(patch).length === 0
                }
              >
                Save details
              </Button>
            </div>
          </form>
        ) : (
          <dl className="flex flex-col gap-3">
            <ReadOnlyRow term="Name" value={flag.name} />
            <ReadOnlyRow term="Description" value={flag.description} />
            <ReadOnlyRow term="Tags" value={flag.tags.join(", ")} />
            <ReadOnlyRow term="Owner" value={flag.owner ?? ""} />
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function VariationRow({
  project,
  flag,
  variation,
  canEdit,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  variation: VariationOut;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const id = useId();
  const fixed = flag.type === "boolean";
  const [name, setName] = useState(variation.name);
  const [description, setDescription] = useState(variation.description);
  const initialValue = formatVariationValue(flag.type, variation.value);
  const [value, setValue] = useState(initialValue);
  const [saving, setSaving] = useState(false);
  const parsed = parseVariationValue(flag.type, value);
  const valueError = parsed.ok ? undefined : parsed.error;
  const key = variation.key;

  const body: S["VariationUpdate"] = {};
  if (name !== variation.name) body.name = name;
  if (description !== variation.description) body.description = description;
  if (!fixed && parsed.ok && value !== initialValue) body.value = parsed.value;
  const path = { project, flag: flag.key, variation: key };

  const save = async () => {
    setSaving(true);
    const saved = await attempt(() =>
      must(
        api.PATCH(
          "/api/projects/{project}/flags/{flag}/variations/{variation}",
          {
            params: { path },
            body,
          },
        ),
      ),
    );
    setSaving(false);
    if (saved) onChanged();
  };

  const remove = async () => {
    const removed = await attempt(
      () =>
        must(
          api.DELETE(
            "/api/projects/{project}/flags/{flag}/variations/{variation}",
            { params: { path } },
          ),
        ),
      `Variation ${key} deleted`,
    );
    if (removed) onChanged();
  };

  if (!canEdit)
    return (
      <li className="flex min-w-0 flex-col gap-1 py-2">
        <code className="font-semibold break-all">{key}</code>
        {variation.name && <span>{variation.name}</span>}
        <code className="text-sm break-all text-muted-foreground">
          {initialValue}
        </code>
      </li>
    );

  return (
    <li className="flex min-w-0 flex-col gap-3 py-3">
      <code className="font-semibold break-all">{key}</code>
      <div className="grid min-w-0 gap-3 md:grid-cols-3">
        <Field id={`${id}-name`} label="Name">
          <Input
            id={`${id}-name`}
            aria-label={`Variation ${key} name`}
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field id={`${id}-description`} label="Description">
          <Input
            id={`${id}-description`}
            aria-label={`Variation ${key} description`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field id={`${id}-value`} label="Value" error={valueError}>
          <Input
            id={`${id}-value`}
            aria-label={`Variation ${key} value`}
            value={value}
            readOnly={fixed}
            onChange={(e) => setValue(e.target.value)}
            {...invalid(`${id}-value`, valueError)}
          />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          className="min-h-11"
          aria-label={`Save variation ${key}`}
          disabled={saving || !!valueError || Object.keys(body).length === 0}
          onClick={save}
        >
          Save
        </Button>
        {!fixed && (
          <ConfirmDialog
            trigger={
              <Button
                variant="outline"
                className="min-h-11"
                aria-label={`Delete variation ${key}`}
              >
                Delete
              </Button>
            }
            title={`Delete variation ${key}?`}
            description="Targeting that serves it must be changed first."
            confirmLabel="Delete"
            destructive
            onConfirm={() => void remove()}
          />
        )}
      </div>
    </li>
  );
}

function AddVariation({
  project,
  flag,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  onChanged: () => void;
}) {
  const id = useId();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [checked, setChecked] = useState(false);
  const [saving, setSaving] = useState(false);

  const parsed = parseVariationValue(flag.type, value);
  const keyError = !KEY_PATTERN.test(key)
    ? "Use lowercase letters, digits, '.', '_' or '-'"
    : flag.variations.some((v) => v.key === key)
      ? "This key is already used"
      : undefined;
  const valueError = parsed.ok ? undefined : parsed.error;
  const errors = {
    key: checked ? keyError : undefined,
    value: checked ? valueError : undefined,
  };

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (keyError || !parsed.ok) return;
    setSaving(true);
    const added = await attempt(
      () =>
        must(
          api.POST("/api/projects/{project}/flags/{flag}/variations", {
            params: { path: { project, flag: flag.key } },
            body: { key, name, description: "", value: parsed.value },
          }),
        ),
      `Variation ${key} added`,
    );
    setSaving(false);
    if (!added) return;
    setKey("");
    setName("");
    setValue("");
    setChecked(false);
    onChanged();
  };

  return (
    <form onSubmit={add} className="flex min-w-0 flex-col gap-3 pt-3">
      <h3 className="font-semibold">New variation</h3>
      <div className="grid min-w-0 gap-3 md:grid-cols-3">
        <Field id={`${id}-key`} label="Key" error={errors.key}>
          <Input
            id={`${id}-key`}
            aria-label="New variation key"
            value={key}
            autoCapitalize="none"
            spellCheck={false}
            onChange={(e) => setKey(e.target.value)}
            {...invalid(`${id}-key`, errors.key)}
          />
        </Field>
        <Field id={`${id}-name`} label="Name">
          <Input
            id={`${id}-name`}
            aria-label="New variation name"
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field id={`${id}-value`} label="Value" error={errors.value}>
          <Input
            id={`${id}-value`}
            aria-label="New variation value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            {...invalid(`${id}-value`, errors.value)}
          />
        </Field>
      </div>
      <div>
        <Button
          type="submit"
          variant="outline"
          className="min-h-11"
          disabled={saving}
        >
          Add variation
        </Button>
      </div>
    </form>
  );
}

function VariationsCard({
  project,
  flag,
  canEdit,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  canEdit: boolean;
  onChanged: () => void;
}) {
  return (
    <Card className="min-w-0 px-4">
      <CardHeader className="px-0">
        <CardTitle>
          <h2>Variations</h2>
        </CardTitle>
        {flag.type === "boolean" && (
          <CardDescription>
            Boolean flags always have the variations true and false.
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex flex-col px-0">
        <ul className="flex flex-col divide-y">
          {flag.variations.map((v) => (
            <VariationRow
              key={v.key}
              project={project}
              flag={flag}
              variation={v}
              canEdit={canEdit}
              onChanged={onChanged}
            />
          ))}
        </ul>
        {canEdit && flag.type !== "boolean" && (
          <AddVariation project={project} flag={flag} onChanged={onChanged} />
        )}
      </CardContent>
    </Card>
  );
}

function OffVariationCard({
  project,
  flag,
  canEdit,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const id = useId();
  const [saving, setSaving] = useState(false);

  const choose = async (off_variation: string) => {
    setSaving(true);
    const saved = await attempt(() =>
      must(
        api.PATCH("/api/projects/{project}/flags/{flag}", {
          params: { path: { project, flag: flag.key } },
          body: { off_variation },
        }),
      ),
    );
    setSaving(false);
    if (saved) onChanged();
  };

  return (
    <Card className="min-w-0 px-4">
      <CardHeader className="px-0">
        <CardTitle>
          <h2>Off variation</h2>
        </CardTitle>
        <CardDescription>Served wherever the flag is off.</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {canEdit ? (
          <div className="flex max-w-sm flex-col gap-2">
            <Label htmlFor={`${id}-off`}>Off variation</Label>
            <NativeSelect
              id={`${id}-off`}
              value={flag.off_variation}
              disabled={saving}
              onChange={(e) => void choose(e.target.value)}
            >
              {flag.variations.map((v) => (
                <option key={v.key} value={v.key}>
                  {v.name ? `${v.name} (${v.key})` : v.key}
                </option>
              ))}
            </NativeSelect>
          </div>
        ) : (
          <code className="break-all">{flag.off_variation}</code>
        )}
      </CardContent>
    </Card>
  );
}

function LifecycleCard({
  project,
  flag,
  canEdit,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [saving, setSaving] = useState(false);

  const mark = async (permanent: boolean) => {
    setSaving(true);
    const saved = await attempt(() =>
      must(
        api.PUT("/api/projects/{project}/flags/{flag}/lifecycle", {
          params: { path: { project, flag: flag.key } },
          body: { permanent },
        }),
      ),
    );
    setSaving(false);
    if (saved) onChanged();
  };

  return (
    <Card className="min-w-0 px-4">
      <CardHeader className="px-0">
        <CardTitle>
          <h2>Lifecycle</h2>
        </CardTitle>
        <CardDescription>
          Permanent flags are never marked stale.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3 px-0">
        {flag.lifecycle === "stale" ? (
          <StatusPill status="stale" />
        ) : (
          <Badge variant="outline">{flag.lifecycle}</Badge>
        )}
        {canEdit && (
          <div className="flex items-center gap-3">
            <Toggle
              label="Permanent flag"
              checked={flag.lifecycle === "permanent"}
              disabled={saving || flag.archived}
              onCheckedChange={(permanent) => void mark(permanent)}
            />
            <span>Permanent flag</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DangerCard({
  project,
  flag,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  onChanged: () => void;
}) {
  const path = { params: { path: { project, flag: flag.key } } };
  const archive = async () => {
    const done = await attempt(
      () =>
        must(api.POST("/api/projects/{project}/flags/{flag}/archive", path)),
      "Flag archived",
    );
    if (done) onChanged();
  };
  const restore = async () => {
    const done = await attempt(
      () =>
        must(api.POST("/api/projects/{project}/flags/{flag}/restore", path)),
      "Flag restored",
    );
    if (done) onChanged();
  };

  return (
    <Card className="min-w-0 border-destructive/40 px-4">
      <CardHeader className="px-0">
        <CardTitle>
          <h2>Danger zone</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2 px-0">
        {flag.archived ? (
          <>
            <ConfirmDialog
              trigger={
                <Button variant="outline" className="min-h-11">
                  Restore flag
                </Button>
              }
              title={`Restore ${flag.key}?`}
              description="It shows in lists again and can be changed."
              confirmLabel="Restore"
              onConfirm={() => void restore()}
            />
            <DeleteFlagDialog project={project} flag={flag.key} />
          </>
        ) : (
          <ConfirmDialog
            trigger={
              <Button variant="destructive" className="min-h-11">
                Archive flag
              </Button>
            }
            title={`Archive ${flag.key}?`}
            description="Archiving hides it from lists and cancels its pending schedules and live rollouts."
            confirmLabel="Archive"
            destructive
            onConfirm={() => void archive()}
          />
        )}
      </CardContent>
    </Card>
  );
}

/**
 * The flag's Settings tab: details, variations, off variation, the permanent mark, and archive,
 * restore and delete. `onChanged` reloads the flag after a change.
 */
export function FlagSettings({
  project,
  flag,
  perms,
  onChanged,
}: {
  project: string;
  flag: FlagOut;
  perms: MePermissions | null;
  onChanged: () => void;
}) {
  const canEdit = can(perms, "flag.update");
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <DetailsCard
        project={project}
        flag={flag}
        canEdit={canEdit}
        onChanged={onChanged}
      />
      <VariationsCard
        project={project}
        flag={flag}
        canEdit={canEdit}
        onChanged={onChanged}
      />
      <OffVariationCard
        project={project}
        flag={flag}
        canEdit={canEdit}
        onChanged={onChanged}
      />
      <LifecycleCard
        project={project}
        flag={flag}
        canEdit={canEdit}
        onChanged={onChanged}
      />
      {can(perms, "flag.archive") && (
        <DangerCard project={project} flag={flag} onChanged={onChanged} />
      )}
    </div>
  );
}
