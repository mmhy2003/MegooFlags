"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import type { EnvState } from "@/components/flags/env-strip";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogClose } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";

type Environment = components["schemas"]["EnvironmentOut"];

/** Why the config can't be copied into `env`, or null when it can. */
export function copyBlocked(
  perms: MePermissions | null,
  env: Environment,
): string | null {
  if (env.approvals_required > 0)
    return "Needs approval: copy is not allowed here";
  if (!can(perms, "flag.config.write", env.key))
    return "You can't change this environment";
  return null;
}

function CopyForm({
  project,
  flag,
  environments,
  selected,
  perms,
  onCopied,
}: {
  project: string;
  flag: string;
  environments: Environment[];
  selected: string;
  perms: MePermissions | null;
  onCopied: (states: EnvState[]) => void;
}) {
  const id = useId();
  const [from, setFrom] = useState(selected);
  const [to, setTo] = useState<string[]>([]);
  const [includeEnabled, setIncludeEnabled] = useState(false);
  const [sending, setSending] = useState(false);
  const [alert, setAlert] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    setAlert(null);
    try {
      const states = await must(
        api.POST("/api/projects/{project}/flags/{flag}/copy", {
          params: { path: { project, flag } },
          body: { from, to, include_enabled: includeEnabled },
        }),
      );
      const names = environments
        .filter((env) => to.includes(env.key))
        .map((env) => env.name);
      toast(`Copied to ${names.join(", ")}`);
      onCopied(states);
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("copy failed", error);
      setAlert(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
      setSending(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {alert && (
        <Alert variant="destructive">
          <AlertDescription>{alert}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-from`}>From</Label>
        <NativeSelect
          id={`${id}-from`}
          value={from}
          onChange={(e) => {
            setFrom(e.target.value);
            setTo((keys) => keys.filter((key) => key !== e.target.value));
          }}
        >
          {environments.map((env) => (
            <option key={env.key} value={env.key}>
              {env.name}
            </option>
          ))}
        </NativeSelect>
      </div>
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">To</legend>
        {environments
          .filter((env) => env.key !== from)
          .map((env) => {
            const blocked = copyBlocked(perms, env);
            const reasonId = `${id}-${env.key}-reason`;
            return (
              <div key={env.key} className="flex flex-col">
                <label
                  data-hit-area
                  className="inline-flex min-h-11 min-w-11 items-center gap-2"
                >
                  <Checkbox
                    checked={to.includes(env.key)}
                    disabled={blocked !== null}
                    aria-describedby={blocked ? reasonId : undefined}
                    onChange={(e) =>
                      setTo((keys) =>
                        e.target.checked
                          ? [...keys, env.key]
                          : keys.filter((key) => key !== env.key),
                      )
                    }
                  />
                  To {env.name}
                </label>
                {blocked && (
                  <p id={reasonId} className="text-sm text-muted-foreground">
                    {blocked}
                  </p>
                )}
              </div>
            );
          })}
      </fieldset>
      <label
        data-hit-area
        className="inline-flex min-h-11 min-w-11 items-center gap-2"
      >
        <Checkbox
          checked={includeEnabled}
          onChange={(e) => setIncludeEnabled(e.target.checked)}
        />
        Include on/off state
      </label>
      <p className="text-sm text-muted-foreground">
        Targets, rules, prerequisites and the default rule overwrite the
        target&apos;s configuration.
      </p>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={sending || to.length === 0}>
          Copy
        </Button>
      </div>
    </form>
  );
}

/**
 * Copies the flag's configuration from one environment to others. Environments that need
 * approval are disabled, because the API refuses copies into them.
 */
export function CopyDialog({
  open,
  onOpenChange,
  project,
  flag,
  environments,
  selected,
  perms,
  onCopied,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  flag: string;
  environments: Environment[];
  /** The environment being edited, the default source. */
  selected: string;
  perms: MePermissions | null;
  /** The target environments' new state, from the API. */
  onCopied: (states: EnvState[]) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Copy configuration"
    >
      {open && (
        <CopyForm
          project={project}
          flag={flag}
          environments={environments}
          selected={selected}
          perms={perms}
          onCopied={onCopied}
        />
      )}
    </ResponsiveDialog>
  );
}
