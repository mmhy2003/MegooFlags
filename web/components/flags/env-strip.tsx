"use client";

import { Lock, OctagonX } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
  type ToggleRequest,
  ToggleRequestDialog,
} from "@/components/flags/toggle-request-dialog";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { StatusPill } from "@/components/patterns/status-pill";
import { Toggle } from "@/components/patterns/toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, failureText, must } from "@/lib/flags/errors";
import { can } from "@/lib/flags/permissions";
import type { MePermissions } from "@/lib/nav";
import { cn } from "@/lib/utils";

type Environment = components["schemas"]["EnvironmentOut"];
export type EnvState = components["schemas"]["FlagEnvironmentState"];

function showFailure(error: unknown) {
  if (!(error instanceof ApiError)) console.error("toggle failed", error);
  toast.error(
    error instanceof ApiError
      ? failureText(error.failure)
      : "Couldn't reach the server",
  );
}

/**
 * A card per environment with the flag's state, a toggle, the kill switch and the button that
 * picks the environment being edited. In an environment that needs approval, the toggle opens a
 * change request instead; the kill switch always goes straight through.
 */
export function EnvStrip({
  project,
  flag,
  environments,
  states,
  perms,
  readOnly,
  selected,
  onSelect,
  onState,
}: {
  project: string;
  flag: string;
  environments: Environment[];
  states: Record<string, EnvState>;
  perms: MePermissions | null;
  /** The flag is archived: nothing can be changed. */
  readOnly: boolean;
  selected: string;
  onSelect: (env: string) => void;
  /** The flag's new state in an environment after a toggle or a kill. */
  onState: (state: EnvState) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [request, setRequest] = useState<ToggleRequest | null>(null);
  const path = (env: string) => ({ path: { project, flag, env } });

  const send = async (env: Environment, call: () => Promise<EnvState>) => {
    setBusy(env.key);
    try {
      const state = await call();
      onState(state);
      toast(`Turned ${state.enabled ? "on" : "off"} in ${env.name}`);
    } catch (error) {
      showFailure(error);
    } finally {
      setBusy(null);
    }
  };

  const toggle = (env: Environment, state: EnvState, enabled: boolean) => {
    if (env.approvals_required > 0) {
      setRequest({
        env: env.key,
        envName: env.name,
        enabled,
        version: state.version,
      });
      return;
    }
    void send(env, () =>
      must(
        api.POST(
          "/api/projects/{project}/flags/{flag}/environments/{env}/toggle",
          {
            params: path(env.key),
            body: { enabled, version: state.version },
          },
        ),
      ),
    );
  };

  const kill = (env: Environment) =>
    void send(env, () =>
      must(
        api.POST(
          "/api/projects/{project}/flags/{flag}/environments/{env}/kill",
          { params: path(env.key) },
        ),
      ),
    );

  return (
    <section
      aria-label="Environments"
      className="grid grid-cols-[repeat(auto-fill,minmax(min(15rem,100%),1fr))] gap-3"
    >
      {environments.map((env) => {
        const state = states[env.key];
        if (!state) return null;
        const mayToggle =
          !readOnly && can(perms, "flag.toggle", env.key) && busy !== env.key;
        const isSelected = env.key === selected;
        return (
          <div
            key={env.key}
            role="group"
            aria-label={env.name}
            className={cn(
              "flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-3",
              isSelected && "border-primary ring-1 ring-primary",
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium break-words">{env.name}</span>
              <StatusPill status={state.enabled ? "on" : "off"} />
            </div>
            {env.approvals_required > 0 && (
              <Badge variant="outline" className="w-fit">
                <Lock aria-hidden />
                Needs approval
              </Badge>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Toggle
                label={`${flag} in ${env.name}`}
                checked={state.enabled}
                disabled={!mayToggle}
                onCheckedChange={(on) => toggle(env, state, on)}
              />
              <ConfirmDialog
                destructive
                title={`Kill ${flag} in ${env.name}?`}
                description={`This turns ${flag} off in ${env.name} right away, without approval. Any running progressive rollout in ${env.name} is paused.`}
                confirmLabel="Kill switch"
                onConfirm={() => kill(env)}
                trigger={
                  <Button
                    variant="destructive"
                    className="min-h-11"
                    disabled={!mayToggle || !state.enabled}
                    aria-label={`Kill switch in ${env.name}`}
                  >
                    <OctagonX aria-hidden />
                    Kill
                  </Button>
                }
              />
            </div>
            <Button
              variant={isSelected ? "secondary" : "outline"}
              className="min-h-11"
              aria-pressed={isSelected}
              onClick={() => onSelect(env.key)}
            >
              Edit {env.name}
            </Button>
          </div>
        );
      })}
      <ToggleRequestDialog
        project={project}
        flag={flag}
        request={request}
        onClose={() => setRequest(null)}
      />
    </section>
  );
}
