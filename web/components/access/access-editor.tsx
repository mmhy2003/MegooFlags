"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { ScopePicker } from "@/components/access/scope-picker";
import { useRoles } from "@/components/access/use-catalogue";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiData } from "@/hooks/use-api-data";
import {
  bindingText,
  formFailureText,
  isFromSso,
  scopeBody,
  type ScopeValue,
} from "@/lib/access/bindings";
import type { Level } from "@/lib/access/permissions";
import { api } from "@/lib/api/client";
import { allPages } from "@/lib/api/pages";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";

type Binding = components["schemas"]["BindingOut"];
type SubjectKind = components["schemas"]["SubjectKind"];

const PROJECT_LEVELS: Level[] = ["project", "environment"];

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
 * The roles a user, team or service token holds, with a form to grant another. Bindings made by
 * SSO sign-in can't be removed here: the identity provider's groups decide them.
 */
export function AccessEditor({
  subjectKind,
  subjectId,
  readOnly,
  lockedProject,
  onChanged,
}: {
  subjectKind: SubjectKind;
  subjectId: string;
  readOnly: boolean;
  /** Keeps new grants in this project, as on a project's keys page. */
  lockedProject?: string;
  onChanged?: () => void;
}) {
  const formId = useId();
  const roles = useRoles();
  const list = useApiData(`${subjectKind}:${subjectId}`, () =>
    allPages((cursor) =>
      must(
        api.GET("/api/bindings", {
          params: {
            query: {
              subject_kind: subjectKind,
              subject_id: subjectId,
              limit: 500,
              cursor,
            },
          },
        }),
      ),
    ),
  );
  const emptyScope: ScopeValue = lockedProject
    ? { scope: "project", project: lockedProject, env: null }
    : { scope: "org", project: null, env: null };
  const [role, setRole] = useState("");
  const [scope, setScope] = useState<ScopeValue>(emptyScope);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The binding stays set while the dialog closes, so its text doesn't change mid-animation.
  const [removing, setRemoving] = useState<{
    open: boolean;
    binding: Binding | null;
  }>({ open: false, binding: null });

  const changed = () => {
    list.reload();
    onChanged?.();
  };

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await must(
        api.POST("/api/bindings", {
          body: {
            subject_kind: subjectKind,
            subject_id: subjectId,
            role,
            ...scopeBody(scope),
          },
        }),
      );
      toast("Access added");
      setRole("");
      setScope(emptyScope);
      changed();
    } catch (e) {
      setError(failureMessage(e, "adding access failed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (b: Binding) => {
    setError(null);
    try {
      await must(
        api.DELETE("/api/bindings/{id}", { params: { path: { id: b.id } } }),
      );
      toast("Access removed");
      changed();
    } catch (e) {
      setError(failureMessage(e, "removing access failed"));
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load access"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading access" rows={2} />;
  } else if (list.data.length === 0) {
    body = <p className="text-muted-foreground">No access yet.</p>;
  } else {
    body = (
      <ul className="flex flex-col divide-y rounded-md border">
        {list.data.map((b) => (
          <li
            key={b.id}
            className="flex min-h-11 flex-wrap items-center gap-2 px-3 py-1"
          >
            <span className="min-w-0 flex-1 break-words">{bindingText(b)}</span>
            {isFromSso(b) && <Badge variant="secondary">From SSO</Badge>}
            {!readOnly && !isFromSso(b) && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Remove ${bindingText(b)}`}
                className="min-h-11 min-w-11"
                onClick={() => setRemoving({ open: true, binding: b })}
              >
                <Trash2 aria-hidden />
              </Button>
            )}
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
      {!readOnly && (
        <form
          aria-label="Add access"
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <div className="flex min-w-0 flex-col gap-2">
            <Label htmlFor={`${formId}-role`}>Role</Label>
            <NativeSelect
              id={`${formId}-role`}
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
            levels={lockedProject ? PROJECT_LEVELS : undefined}
            lockedProject={lockedProject}
            idPrefix={`${formId}-scope`}
          />
          <div>
            <Button
              type="submit"
              disabled={busy || role === "" || !isComplete(scope)}
            >
              <Plus aria-hidden />
              Add
            </Button>
          </div>
        </form>
      )}
      <ConfirmDialog
        open={removing.open}
        onOpenChange={(open) => setRemoving((r) => ({ ...r, open }))}
        title="Remove access?"
        description={`${removing.binding ? bindingText(removing.binding) : "This role"} is taken away at once.`}
        confirmLabel="Remove access"
        destructive
        onConfirm={() => {
          if (removing.binding) void remove(removing.binding);
        }}
      />
    </div>
  );
}
