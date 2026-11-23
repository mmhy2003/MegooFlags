"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { ScopePicker } from "@/components/access/scope-picker";
import { useRoles } from "@/components/access/use-catalogue";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiData } from "@/hooks/use-api-data";
import {
  formFailureText,
  scopeBody,
  type ScopeValue,
} from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import { allPages } from "@/lib/api/pages";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";
import { mappingText, sortMappings } from "@/lib/settings/mappings";

type Provider = components["schemas"]["SsoProviderOut"];
type Mapping = components["schemas"]["SsoMappingOut"];
type Target = "team" | "role";

const ORG_SCOPE: ScopeValue = { scope: "org", project: null, env: null };

function failureMessage(error: unknown, what: string): string {
  if (error instanceof ApiError) return formFailureText(error.failure);
  console.error(what, error);
  return "Couldn't reach the server";
}

/** Whether the scope names everything its level needs. */
function isComplete(s: ScopeValue): boolean {
  if (s.scope === "org") return true;
  if (s.scope === "project") return s.project !== null;
  return s.project !== null && s.env !== null;
}

/**
 * A provider's IdP group mappings: each group the identity provider sends puts its members in a
 * team, or gives them a role at a scope, when they sign in.
 */
export function MappingsDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: Provider | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Group mappings"
      description={
        provider &&
        `What the groups ${provider.name} sends give people when they sign in.`
      }
    >
      {open && provider && <Mappings providerKey={provider.key} />}
    </ResponsiveDialog>
  );
}

function Mappings({ providerKey }: { providerKey: string }) {
  const id = useId();
  const roles = useRoles();
  const teams = useApiData("teams", () =>
    must(api.GET("/api/teams", { params: { query: { limit: 500 } } })).then(
      (page) => page.items,
    ),
  ).data;
  const list = useApiData(`sso-mappings:${providerKey}`, () =>
    allPages((cursor) =>
      must(
        api.GET("/api/sso/providers/{key}/mappings", {
          params: { path: { key: providerKey }, query: { limit: 500, cursor } },
        }),
      ),
    ).then(sortMappings),
  );
  const [group, setGroup] = useState("");
  const [target, setTarget] = useState<Target>("team");
  const [team, setTeam] = useState("");
  const [role, setRole] = useState("");
  const [scope, setScope] = useState<ScopeValue>(ORG_SCOPE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The mapping stays set while the dialog closes, so its text doesn't change mid-animation.
  const [removing, setRemoving] = useState<{
    open: boolean;
    mapping: Mapping | null;
  }>({ open: false, mapping: null });

  const ready =
    group.trim() !== "" &&
    (target === "team" ? team !== "" : role !== "" && isComplete(scope));

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await must(
        api.POST("/api/sso/providers/{key}/mappings", {
          params: { path: { key: providerKey } },
          body:
            target === "team"
              ? { group: group.trim(), team }
              : { group: group.trim(), role, ...scopeBody(scope) },
        }),
      );
      toast("Mapping added");
      setGroup("");
      setTeam("");
      setRole("");
      setScope(ORG_SCOPE);
      list.reload();
    } catch (e) {
      setError(failureMessage(e, "adding the group mapping failed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m: Mapping) => {
    setError(null);
    try {
      await must(
        api.DELETE("/api/sso/providers/{key}/mappings/{id}", {
          params: { path: { key: providerKey, id: m.id } },
        }),
      );
      toast("Mapping removed");
      list.reload();
    } catch (e) {
      setError(failureMessage(e, "removing the group mapping failed"));
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load group mappings"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading group mappings" rows={2} />;
  } else if (list.data.length === 0) {
    body = <p className="text-muted-foreground">No group mappings yet.</p>;
  } else {
    body = (
      <ul
        aria-label="Group mappings"
        className="flex flex-col divide-y rounded-md border"
      >
        {list.data.map((m) => (
          <li
            key={m.id}
            className="flex min-h-11 flex-wrap items-center gap-2 px-3 py-1"
          >
            <span className="min-w-0 flex-1 break-words">{mappingText(m)}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove ${mappingText(m)}`}
              className="min-h-11 min-w-11"
              onClick={() => setRemoving({ open: true, mapping: m })}
            >
              <Trash2 aria-hidden />
            </Button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {body}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <form
        aria-label="Add a mapping"
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <div className="flex min-w-0 flex-col gap-2">
          <Label htmlFor={`${id}-group`}>Group</Label>
          <Input
            id={`${id}-group`}
            autoComplete="off"
            value={group}
            placeholder="developers"
            onChange={(e) => setGroup(e.target.value)}
          />
          <p className="text-sm text-muted-foreground">
            As the identity provider names it.
          </p>
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-sm font-medium">Gives</legend>
          <div
            role="radiogroup"
            aria-label="Gives"
            className="flex flex-wrap gap-x-6"
          >
            {(
              [
                { value: "team", label: "A team" },
                { value: "role", label: "A role" },
              ] as const
            ).map((o) => (
              <label
                key={o.value}
                data-hit-area
                className="inline-flex min-h-11 min-w-11 items-center gap-2"
              >
                <input
                  type="radio"
                  name={`${id}-target`}
                  className="size-5 shrink-0 accent-primary"
                  checked={target === o.value}
                  onChange={() => setTarget(o.value)}
                />
                {o.label}
              </label>
            ))}
          </div>
        </fieldset>
        {target === "team" ? (
          <div className="flex min-w-0 flex-col gap-2">
            <Label htmlFor={`${id}-team`}>Team</Label>
            <NativeSelect
              id={`${id}-team`}
              value={team}
              onChange={(e) => setTeam(e.target.value)}
            >
              <option value="">Pick a team</option>
              {(teams ?? []).map((t) => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </NativeSelect>
          </div>
        ) : (
          <>
            <div className="flex min-w-0 flex-col gap-2">
              <Label htmlFor={`${id}-role`}>Role</Label>
              <NativeSelect
                id={`${id}-role`}
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="">Pick a role</option>
                {(roles ?? []).map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <ScopePicker
              value={scope}
              onChange={setScope}
              idPrefix={`${id}-scope`}
            />
          </>
        )}
        <div>
          <Button type="submit" disabled={busy || !ready}>
            <Plus aria-hidden />
            Add mapping
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={removing.open}
        onOpenChange={(open) => setRemoving((r) => ({ ...r, open }))}
        title="Remove the mapping?"
        description={`${removing.mapping ? mappingText(removing.mapping) : "The group"} no longer applies to people who sign in.`}
        confirmLabel="Remove mapping"
        destructive
        onConfirm={() => {
          if (removing.mapping) void remove(removing.mapping);
        }}
      />
    </div>
  );
}
