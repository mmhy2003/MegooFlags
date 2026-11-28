"use client";

import { useRouter } from "next/navigation";
import { Lock, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/patterns/empty-state";
import { useProjectPermissions } from "@/components/shell/use-project-permissions";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import {
  type FlagForm as Form,
  formErrors,
  parseTags,
  slugifyKey,
  toFlagCreate,
  type VariationRow,
} from "@/lib/flags/flag-form";
import { can } from "@/lib/flags/permissions";
import { flagPath } from "@/lib/routes";

type FlagType = components["schemas"]["FlagType"];

const TYPES: { value: FlagType; label: string }[] = [
  { value: "boolean", label: "Boolean" },
  { value: "string", label: "String" },
  { value: "number", label: "Number" },
  { value: "json", label: "JSON" },
];

const STEPS = 3;

const EMPTY_ROW: VariationRow = { key: "", name: "", value: "" };

const EMPTY_FORM: Form = {
  key: "",
  keyEdited: false,
  name: "",
  description: "",
  type: "boolean",
  tags: [],
  owner: "",
  variations: [EMPTY_ROW, EMPTY_ROW],
  offVariation: "",
};

/** The step a form field belongs to: Details (0) or Variations (1). */
function stepOf(field: string): number {
  return field === "key" || field === "name" ? 0 : 1;
}

/** Where a 422 path lands: a variation row's field, or null for the form alert. */
function rowField(path: string | null): string | null {
  const match = path?.match(/^variations\[(\d+)\](?:\.(key|name|value))?/);
  if (!match) return null;
  return `variations[${match[1]}].${match[2] ?? "key"}`;
}

/** A label, a control and its error, linked by id. */
function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error: string | undefined;
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

/** Props that tie a control to its error. */
function invalid(id: string, error: string | undefined) {
  return error
    ? { "aria-invalid": true, "aria-describedby": `${id}-error` }
    : {};
}

/** The New flag form: one page on desktop, a three-step stepper below 768 px. */
export function FlagForm({ project }: { project: string }) {
  const perms = useProjectPermissions(project, null, null);
  if (!perms) {
    return (
      <div role="status" className="flex flex-col gap-3">
        <span className="sr-only">Loading</span>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (!can(perms, "flag.create")) {
    return (
      <EmptyState
        icon={Lock}
        title="You can't create flags in this project"
        description="Ask a project admin for a role that can create flags."
      />
    );
  }
  return <NewFlagForm project={project} />;
}

function NewFlagForm({ project }: { project: string }) {
  const router = useRouter();
  const id = useId();
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [tagsText, setTagsText] = useState("");
  const [step, setStep] = useState(0);
  // Steps whose errors are shown: a step's after Next on it, every step after Create flag.
  const [checked, setChecked] = useState<number[]>([]);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [alert, setAlert] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const local = formErrors(form);
  const errors: Record<string, string> = { ...serverErrors };
  for (const [field, message] of Object.entries(local)) {
    if (checked.includes(stepOf(field))) errors[field] = message;
  }

  const update = (patch: Partial<Form>) => {
    setForm((f) => ({ ...f, ...patch }));
    setServerErrors({});
  };
  const setName = (name: string) =>
    update(form.keyEdited ? { name } : { name, key: slugifyKey(name) });
  const setRow = (i: number, patch: Partial<VariationRow>) => {
    const old = form.variations[i];
    update({
      variations: form.variations.map((v, j) =>
        j === i ? { ...v, ...patch } : v,
      ),
      // Renaming the off variation's key keeps it the off variation.
      offVariation:
        patch.key !== undefined &&
        old.key !== "" &&
        old.key === form.offVariation
          ? patch.key
          : form.offVariation,
    });
  };
  const removeRow = (i: number) =>
    update({
      variations: form.variations.filter((_, j) => j !== i),
      offVariation:
        form.variations[i].key === form.offVariation ? "" : form.offVariation,
    });

  const next = () => {
    setChecked((c) => [...new Set([...c, step])]);
    if (Object.keys(local).some((field) => stepOf(field) === step)) return;
    setStep((s) => Math.min(s + 1, STEPS - 1));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked([0, 1]);
    setAlert(null);
    const fields = Object.keys(local);
    if (fields.length > 0) {
      setStep(Math.min(...fields.map(stepOf)));
      return;
    }
    setBusy(true);
    try {
      const flag = await must(
        api.POST("/api/projects/{project}/flags", {
          params: { path: { project } },
          body: toFlagCreate(form),
        }),
      );
      toast("Flag created");
      router.push(flagPath(project, flag.key));
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) {
        setAlert("Couldn't reach the server");
        return;
      }
      const { failure } = error;
      const row = failure.status === 422 ? rowField(failure.path) : null;
      if (failure.status === 409 && failure.code === "key_conflict") {
        setServerErrors({ key: "A flag with this key already exists" });
        setStep(0);
      } else if (row) {
        setServerErrors({ [row]: failure.message });
        setStep(1);
      } else {
        setAlert(failureText(failure));
      }
    }
  };

  const section = (i: number) =>
    `flex flex-col gap-4 ${step === i ? "" : "max-md:hidden"}`;
  const keys = form.variations.map((v) => v.key).filter(Boolean);
  const typeLabel = TYPES.find((t) => t.value === form.type)?.label;

  return (
    <form
      noValidate
      onSubmit={submit}
      className="flex max-w-3xl flex-col gap-8"
    >
      <nav
        aria-label="Steps"
        className="flex flex-wrap items-center justify-between gap-2 md:hidden"
      >
        <p className="text-sm font-medium">
          Step {step + 1} of {STEPS}
        </p>
        <div className="flex gap-2">
          {step > 0 && (
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              onClick={() => setStep((s) => s - 1)}
            >
              Back
            </Button>
          )}
          {step < STEPS - 1 && (
            <Button type="button" className="min-h-11" onClick={next}>
              Next
            </Button>
          )}
        </div>
      </nav>

      <section aria-labelledby={`${id}-details`} className={section(0)}>
        <h2 id={`${id}-details`} className="text-lg font-semibold">
          Details
        </h2>
        <Field id={`${id}-name`} label="Name" error={errors.name}>
          <Input
            id={`${id}-name`}
            value={form.name}
            onChange={(e) => setName(e.target.value)}
            {...invalid(`${id}-name`, errors.name)}
          />
        </Field>
        <Field id={`${id}-key`} label="Key" error={errors.key}>
          <Input
            id={`${id}-key`}
            value={form.key}
            autoCapitalize="none"
            spellCheck={false}
            className="font-mono"
            onChange={(e) => update({ key: e.target.value, keyEdited: true })}
            {...invalid(`${id}-key`, errors.key)}
          />
        </Field>
        <Field id={`${id}-description`} label="Description" error={undefined}>
          <Textarea
            id={`${id}-description`}
            value={form.description}
            className="md:text-base"
            onChange={(e) => update({ description: e.target.value })}
          />
        </Field>
        <Field id={`${id}-type`} label="Type" error={undefined}>
          <NativeSelect
            id={`${id}-type`}
            value={form.type}
            onChange={(e) => update({ type: e.target.value as FlagType })}
          >
            {TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id={`${id}-tags`} label="Tags" error={undefined}>
          <Input
            id={`${id}-tags`}
            value={tagsText}
            placeholder="web, checkout"
            onChange={(e) => {
              setTagsText(e.target.value);
              update({ tags: parseTags(e.target.value) });
            }}
          />
        </Field>
        <Field id={`${id}-owner`} label="Owner" error={undefined}>
          <Input
            id={`${id}-owner`}
            value={form.owner}
            onChange={(e) => update({ owner: e.target.value })}
          />
        </Field>
      </section>

      <section aria-labelledby={`${id}-variations`} className={section(1)}>
        <h2 id={`${id}-variations`} className="text-lg font-semibold">
          Variations
        </h2>
        {form.type === "boolean" ? (
          <p className="text-muted-foreground">
            Boolean flags serve true or false.
          </p>
        ) : (
          <>
            <ol className="flex flex-col gap-4">
              {form.variations.map((v, i) => {
                const n = i + 1;
                const row = `${id}-variation-${i}`;
                const err = (f: string) => errors[`variations[${i}].${f}`];
                return (
                  <li
                    key={i}
                    className="grid gap-3 rounded-lg border p-3 md:grid-cols-[1fr_1fr_1fr_auto] md:items-start"
                  >
                    <Field id={`${row}-key`} label="Key" error={err("key")}>
                      <Input
                        id={`${row}-key`}
                        aria-label={`Variation ${n} key`}
                        value={v.key}
                        autoCapitalize="none"
                        spellCheck={false}
                        className="font-mono"
                        onChange={(e) => setRow(i, { key: e.target.value })}
                        {...invalid(`${row}-key`, err("key"))}
                      />
                    </Field>
                    <Field id={`${row}-name`} label="Name" error={err("name")}>
                      <Input
                        id={`${row}-name`}
                        aria-label={`Variation ${n} name`}
                        value={v.name}
                        onChange={(e) => setRow(i, { name: e.target.value })}
                        {...invalid(`${row}-name`, err("name"))}
                      />
                    </Field>
                    <Field
                      id={`${row}-value`}
                      label="Value"
                      error={err("value")}
                    >
                      {form.type === "json" ? (
                        <Textarea
                          id={`${row}-value`}
                          aria-label={`Variation ${n} value`}
                          value={v.value}
                          spellCheck={false}
                          className="font-mono md:text-base"
                          onChange={(e) => setRow(i, { value: e.target.value })}
                          {...invalid(`${row}-value`, err("value"))}
                        />
                      ) : (
                        <Input
                          id={`${row}-value`}
                          aria-label={`Variation ${n} value`}
                          value={v.value}
                          inputMode={
                            form.type === "number" ? "decimal" : undefined
                          }
                          onChange={(e) => setRow(i, { value: e.target.value })}
                          {...invalid(`${row}-value`, err("value"))}
                        />
                      )}
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      className="min-h-11 min-w-11 md:mt-6"
                      aria-label={`Remove variation ${n}`}
                      disabled={form.variations.length <= 1}
                      onClick={() => removeRow(i)}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </li>
                );
              })}
            </ol>
            <Button
              type="button"
              variant="outline"
              className="min-h-11 self-start"
              onClick={() =>
                update({ variations: [...form.variations, EMPTY_ROW] })
              }
            >
              <Plus aria-hidden />
              Add variation
            </Button>
            <Field
              id={`${id}-off`}
              label="Off variation"
              error={errors.offVariation}
            >
              <NativeSelect
                id={`${id}-off`}
                value={
                  keys.includes(form.offVariation) ? form.offVariation : ""
                }
                onChange={(e) => update({ offVariation: e.target.value })}
                {...invalid(`${id}-off`, errors.offVariation)}
              >
                <option value="">Choose a variation</option>
                {[...new Set(keys)].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </>
        )}
      </section>

      <section aria-labelledby={`${id}-review`} className={section(2)}>
        <h2 id={`${id}-review`} className="text-lg font-semibold">
          Review
        </h2>
        <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Name</dt>
          <dd className="break-words">{form.name || "—"}</dd>
          <dt className="text-muted-foreground">Key</dt>
          <dd className="font-mono break-all">{form.key || "—"}</dd>
          <dt className="text-muted-foreground">Type</dt>
          <dd>{typeLabel}</dd>
          <dt className="text-muted-foreground">Tags</dt>
          <dd className="break-words">{form.tags.join(", ") || "—"}</dd>
          <dt className="text-muted-foreground">Owner</dt>
          <dd className="break-words">{form.owner.trim() || "—"}</dd>
          <dt className="text-muted-foreground">Variations</dt>
          <dd className="font-mono break-all">
            {form.type === "boolean" ? "true, false" : keys.join(", ") || "—"}
          </dd>
          {form.type !== "boolean" && (
            <>
              <dt className="text-muted-foreground">Off variation</dt>
              <dd className="font-mono break-all">
                {form.offVariation || "—"}
              </dd>
            </>
          )}
        </dl>
        {alert && (
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t create the flag</AlertTitle>
            <AlertDescription>{alert}</AlertDescription>
          </Alert>
        )}
        <Button type="submit" className="min-h-11 self-start" disabled={busy}>
          Create flag
        </Button>
      </section>
    </form>
  );
}
