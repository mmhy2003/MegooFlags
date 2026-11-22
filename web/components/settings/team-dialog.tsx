"use client";

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { AccessEditor } from "@/components/access/access-editor";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { LoadFailed, LoadingRows } from "@/components/patterns/list-state";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useApiData } from "@/hooks/use-api-data";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { KEY_PATTERN, slugifyKey } from "@/lib/flags/flag-form";

type Team = components["schemas"]["TeamOut"];
type Member = components["schemas"]["TeamMemberOut"];

const NAME_MAX = 200;
const SEARCH_DELAY_MS = 200;
const SEARCH_LIMIT = 10;
const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

function failureMessage(error: unknown, what: string): string {
  if (error instanceof ApiError) return formFailureText(error.failure);
  console.error(what, error);
  return "Couldn't reach the server";
}

/** "Added by hand", or "From SSO (okta)" for a membership that group sync manages. */
function sourceText(source: string): string {
  if (source.startsWith("sso:")) return `From SSO (${source.slice(4)})`;
  return "Added by hand";
}

/**
 * A new team (`team` null) or one team: its name, members and roles. Without `canManage` it
 * only shows them.
 */
export function TeamDialog({
  team,
  open,
  onOpenChange,
  canManage,
  onChanged,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  team: Team | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
  /** Called after each change, so the list shows it. */
  onChanged: () => void;
}) {
  // A rename shows in the title before the list reloads.
  const [names, setNames] = useState<Record<string, string>>({});
  const title = team ? (names[team.id] ?? team.name) : "New team";
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={
        team
          ? team.key
          : "Its members get every role the team holds. You can add them next."
      }
    >
      {open &&
        (team ? (
          <Manage
            key={team.id}
            team={team}
            name={title}
            canManage={canManage}
            onRenamed={(name) => setNames((n) => ({ ...n, [team.id]: name }))}
            onDeleted={() => onOpenChange(false)}
            onChanged={onChanged}
          />
        ) : (
          <CreateForm
            onCreated={() => {
              onOpenChange(false);
              onChanged();
            }}
          />
        ))}
    </ResponsiveDialog>
  );
}

type Field = "name" | "key";
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
  if (f.status === 422) return f.path === "name" ? "name" : "key";
  return null;
}

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
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
      await must(api.POST("/api/teams", { body: { key, name } }));
      toast("Team created");
      onCreated();
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("creating the team failed", err);
        setFailure("Couldn't reach the server");
      } else {
        const field = failureField(err.failure);
        if (field === null) setFailure(formFailureText(err.failure));
        else if (err.failure.code === "key_conflict")
          setServer({ key: "Another team already uses this key" });
        else setServer({ [field]: err.failure.message });
      }
    } finally {
      setBusy(false);
    }
  };

  const field = (
    which: Field,
    label: string,
    input: React.ComponentProps<typeof Input>,
  ) => {
    const errorId = `${id}-${which}-error`;
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-${which}`}>{label}</Label>
        <Input
          id={`${id}-${which}`}
          autoComplete="off"
          {...input}
          {...(errors[which] && {
            "aria-invalid": true,
            "aria-describedby": errorId,
          })}
        />
        {errors[which] && (
          <p id={errorId} className="text-sm text-destructive">
            {errors[which]}
          </p>
        )}
      </div>
    );
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {field("name", "Name", {
        value: name,
        placeholder: "Platform",
        onChange: (e) => {
          setName(e.target.value);
          if (!keyEdited) setKey(slugifyKey(e.target.value));
          setServer((s) => ({ ...s, name: undefined }));
        },
      })}
      {field("key", "Key", {
        value: key,
        autoCapitalize: "none",
        spellCheck: false,
        className: "font-mono",
        onChange: (e) => {
          setKey(e.target.value);
          setKeyEdited(true);
          setServer((s) => ({ ...s, key: undefined }));
        },
      })}
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
          Create team
        </Button>
      </div>
    </form>
  );
}

function Manage({
  team,
  name: currentName,
  canManage,
  onRenamed,
  onDeleted,
  onChanged,
}: {
  team: Team;
  name: string;
  canManage: boolean;
  onRenamed: (name: string) => void;
  onDeleted: () => void;
  onChanged: () => void;
}) {
  const id = useId();
  const [name, setName] = useState(currentName);
  const [nameError, setNameError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);

  /** Runs one action, showing its failure in the dialog. */
  const run = async (what: string, action: () => Promise<void>) => {
    setBusy(true);
    setFailure(null);
    try {
      await action();
    } catch (e) {
      setFailure(failureMessage(e, what));
    } finally {
      setBusy(false);
    }
  };

  const rename = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() === "") return setNameError("Enter a name");
    if (name.length > NAME_MAX)
      return setNameError(`Use at most ${NAME_MAX} characters`);
    setNameError(null);
    void run("renaming the team failed", async () => {
      const out = await must(
        api.PATCH("/api/teams/{key}", {
          params: { path: { key: team.key } },
          body: { name },
        }),
      );
      onRenamed(out.name);
      toast("Team renamed");
      onChanged();
    });
  };

  const remove = () =>
    run("deleting the team failed", async () => {
      await must(
        api.DELETE("/api/teams/{key}", { params: { path: { key: team.key } } }),
      );
      toast("Team deleted");
      onDeleted();
      onChanged();
    });

  const nameErrorId = `${id}-name-error`;

  return (
    <div className="flex flex-col gap-5">
      {canManage && (
        <form noValidate onSubmit={rename} className="flex flex-col gap-2">
          <Label htmlFor={`${id}-name`}>Name</Label>
          <div className="flex flex-wrap gap-2">
            <Input
              id={`${id}-name`}
              className="min-w-0 flex-1"
              autoComplete="off"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameError(null);
              }}
              {...(nameError && {
                "aria-invalid": true,
                "aria-describedby": nameErrorId,
              })}
            />
            <Button
              type="submit"
              variant="outline"
              disabled={busy || name === currentName}
            >
              Save
            </Button>
          </div>
          {nameError && (
            <p id={nameErrorId} className="text-sm text-destructive">
              {nameError}
            </p>
          )}
        </form>
      )}

      <section
        aria-labelledby={`${id}-members`}
        className="flex flex-col gap-3"
      >
        <h3 id={`${id}-members`} className="font-medium">
          Members
        </h3>
        <Members team={team} canManage={canManage} onChanged={onChanged} />
      </section>

      <section aria-labelledby={`${id}-access`} className="flex flex-col gap-3">
        <h3 id={`${id}-access`} className="font-medium">
          Access
        </h3>
        <AccessEditor
          subjectKind="team"
          subjectId={team.id}
          readOnly={!canManage}
          onChanged={onChanged}
        />
      </section>

      {canManage && (
        <>
          <Separator />
          {failure && (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
          <div>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => setDeleting(true)}
            >
              Delete team
            </Button>
          </div>
          <ConfirmDialog
            open={deleting}
            onOpenChange={setDeleting}
            title={`Delete ${currentName}?`}
            description="Its members lose the roles the team gives them. SSO group mappings to it stop working."
            confirmLabel="Delete team"
            destructive
            onConfirm={() => void remove()}
          />
        </>
      )}
    </div>
  );
}

/** The team's members, with an email search to add one and a button to remove each. */
function Members({
  team,
  canManage,
  onChanged,
}: {
  team: Team;
  canManage: boolean;
  onChanged: () => void;
}) {
  const list = useApiData(`team-members:${team.key}`, () =>
    must(
      api.GET("/api/teams/{key}/members", {
        params: { path: { key: team.key }, query: { limit: 500 } },
      }),
    ).then((page) => page.items),
  );
  const [error, setError] = useState<string | null>(null);
  // The member stays set while the dialog closes, so its text doesn't change mid-animation.
  const [removing, setRemoving] = useState<{
    open: boolean;
    member: Member | null;
  }>({ open: false, member: null });

  const changed = () => {
    list.reload();
    onChanged();
  };

  const add = async (userId: string) => {
    setError(null);
    try {
      await must(
        api.POST("/api/teams/{key}/members", {
          params: { path: { key: team.key } },
          body: { user_id: userId },
        }),
      );
      toast("Member added");
      changed();
    } catch (e) {
      setError(failureMessage(e, "adding a team member failed"));
    }
  };

  const remove = async (m: Member) => {
    setError(null);
    try {
      await must(
        api.DELETE("/api/teams/{key}/members/{user_id}", {
          params: { path: { key: team.key, user_id: m.user.id } },
        }),
      );
      toast("Member removed");
      changed();
    } catch (e) {
      setError(failureMessage(e, "removing a team member failed"));
    }
  };

  let body: React.ReactNode;
  if (list.failure) {
    body = (
      <LoadFailed
        title="Couldn't load the members"
        failure={list.failure}
        onRetry={list.reload}
      />
    );
  } else if (!list.data) {
    body = <LoadingRows label="Loading the members" rows={2} />;
  } else if (list.data.length === 0) {
    body = <p className="text-muted-foreground">No members yet.</p>;
  } else {
    body = (
      <ul
        aria-label="Team members"
        className="flex flex-col divide-y rounded-md border"
      >
        {list.data.map((m) => (
          <li
            key={m.user.id}
            className="flex min-h-11 flex-wrap items-center gap-2 px-3 py-1"
          >
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="break-words">{m.user.name}</span>
              <span className="text-sm break-all text-muted-foreground">
                {m.user.email}
              </span>
            </div>
            <Badge variant="secondary">{sourceText(m.source)}</Badge>
            {canManage && m.source === "manual" && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Remove ${m.user.email}`}
                className="min-h-11 min-w-11"
                onClick={() => setRemoving({ open: true, member: m })}
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
      {canManage && list.data && (
        <MemberSearch
          exclude={new Set(list.data.map((m) => m.user.id))}
          onAdd={(userId) => void add(userId)}
        />
      )}
      <ConfirmDialog
        open={removing.open}
        onOpenChange={(open) => setRemoving((r) => ({ ...r, open }))}
        title="Remove from the team?"
        description={`${removing.member?.user.name ?? "They"} loses the roles the team gives them.`}
        confirmLabel="Remove"
        destructive
        onConfirm={() => {
          if (removing.member) void remove(removing.member);
        }}
      />
    </div>
  );
}

/** Finds people by name or email, after a short pause, and offers to add the ones not in the team. */
function MemberSearch({
  exclude,
  onAdd,
}: {
  exclude: Set<string>;
  onAdd: (userId: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const found = useApiData(q === "" ? null : `team-search:${q}`, () =>
    must(
      api.GET("/api/users", {
        params: { query: { q, limit: SEARCH_LIMIT } },
      }),
    ).then((page) => page.items),
  );
  const results = (found.data ?? []).filter((u) => !exclude.has(u.id));

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>Add a member</Label>
      <Input
        id={id}
        type="search"
        autoComplete="off"
        placeholder="Search by name or email"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {q !== "" && found.failure && (
        <p role="alert" className="text-sm text-destructive">
          {formFailureText(found.failure)}
        </p>
      )}
      {q !== "" && found.data && results.length === 0 && (
        <p className="text-sm text-muted-foreground">No one else matches.</p>
      )}
      {q !== "" && results.length > 0 && (
        <ul
          aria-label="People found"
          className="flex flex-col divide-y rounded-md border"
        >
          {results.map((u) => (
            <li
              key={u.id}
              className="flex min-h-11 flex-wrap items-center gap-2 px-3 py-1"
            >
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="break-words">{u.name}</span>
                <span className="text-sm break-all text-muted-foreground">
                  {u.email}
                </span>
              </div>
              <Button
                type="button"
                variant="outline"
                className="min-h-11"
                aria-label={`Add ${u.email}`}
                onClick={() => {
                  onAdd(u.id);
                  setText("");
                }}
              >
                <Plus aria-hidden />
                Add
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
