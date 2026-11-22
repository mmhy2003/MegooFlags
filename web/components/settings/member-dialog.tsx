"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { AccessEditor } from "@/components/access/access-editor";
import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { SecretReveal } from "@/components/patterns/secret-reveal";
import { StatusPill } from "@/components/patterns/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, must } from "@/lib/flags/errors";
import { linkKind, memberPill, memberState } from "@/lib/settings/members";

type User = components["schemas"]["UserOut"];

const NAME_MAX = 200;

const LINKS = {
  invite: { button: "New invite link", title: "Invite link" },
  reset: { button: "Password reset link", title: "Password reset link" },
} as const;

const DATE_TIME = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function failureMessage(error: unknown, what: string): string {
  if (error instanceof ApiError) return formFailureText(error.failure);
  console.error(what, error);
  return "Couldn't reach the server";
}

/**
 * Manages one member: their name, the roles they hold, a new invite or password reset link, a
 * two-factor reset, and disabling or enabling them.
 */
export function MemberDialog({
  user,
  open,
  onOpenChange,
  onChanged,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  user: User | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after each change, so the list shows it. */
  onChanged: () => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={user?.name ?? "Member"}
      description={user?.email}
    >
      {open && user && (
        <Manage key={user.id} user={user} onChanged={onChanged} />
      )}
    </ResponsiveDialog>
  );
}

function Manage({ user, onChanged }: { user: User; onChanged: () => void }) {
  const id = useId();
  // The latest copy the API returned, so the dialog shows a change before the list reloads.
  const [current, setCurrent] = useState(user);
  const [name, setName] = useState(user.name);
  const [nameError, setNameError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<{ url: string; expiresAt: string } | null>(
    null,
  );
  const [confirming, setConfirming] = useState<"mfa" | "disable" | null>(null);

  const kind = linkKind(current);
  const state = memberState(current, new Date());
  const disabled = current.status === "disabled";

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

  const saveName = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() === "") return setNameError("Enter a name");
    if (name.length > NAME_MAX)
      return setNameError(`Use at most ${NAME_MAX} characters`);
    setNameError(null);
    void run("renaming the member failed", async () => {
      const out = await must(
        api.PATCH("/api/users/{id}", {
          params: { path: { id: current.id } },
          body: { name },
        }),
      );
      setCurrent(out);
      toast("Name saved");
      onChanged();
    });
  };

  const newLink = () =>
    run("issuing a link failed", async () => {
      const out = await must(
        api.POST("/api/users/{id}/reset-link", {
          params: { path: { id: current.id } },
        }),
      );
      setLink({ url: out.url, expiresAt: out.expires_at });
      // A new invitation renews an expired one.
      if (kind === "invite")
        setCurrent((c) => ({ ...c, invitation_expires_at: out.expires_at }));
      onChanged();
    });

  const resetMfa = () =>
    run("resetting two-factor failed", async () => {
      await must(
        api.DELETE("/api/users/{id}/mfa", {
          params: { path: { id: current.id } },
        }),
      );
      toast("Two-factor reset");
      onChanged();
    });

  const setEnabled = (enable: boolean) =>
    run(
      enable ? "enabling the member failed" : "disabling the member failed",
      async () => {
        const path = { params: { path: { id: current.id } } };
        const out = await must(
          enable
            ? api.POST("/api/users/{id}/enable", path)
            : api.POST("/api/users/{id}/disable", path),
        );
        setCurrent(out);
        toast(enable ? "Member enabled" : "Member disabled");
        onChanged();
      },
    );

  const nameErrorId = `${id}-name-error`;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <StatusPill {...memberPill(state)} />
      </div>

      <form noValidate onSubmit={saveName} className="flex flex-col gap-2">
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
            disabled={busy || name === current.name}
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

      <section aria-labelledby={`${id}-access`} className="flex flex-col gap-3">
        <h3 id={`${id}-access`} className="font-medium">
          Access
        </h3>
        <AccessEditor
          subjectKind="user"
          subjectId={current.id}
          readOnly={false}
          onChanged={onChanged}
        />
      </section>

      <Separator />

      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void newLink()}
        >
          {LINKS[kind].button}
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => setConfirming("mfa")}
        >
          Reset two-factor
        </Button>
        {disabled ? (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void setEnabled(true)}
          >
            Enable
          </Button>
        ) : (
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => setConfirming("disable")}
          >
            Disable
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirming === "mfa"}
        onOpenChange={(next) => !next && setConfirming(null)}
        title="Reset two-factor?"
        description={`${current.name}'s second factors and recovery codes are removed, and they are signed out. They sign in with their password alone next time.`}
        confirmLabel="Reset two-factor"
        destructive
        onConfirm={() => void resetMfa()}
      />
      <ConfirmDialog
        open={confirming === "disable"}
        onOpenChange={(next) => !next && setConfirming(null)}
        title={`Disable ${current.name}?`}
        description="They are signed out everywhere."
        confirmLabel="Disable"
        destructive
        onConfirm={() => void setEnabled(false)}
      />
      <SecretReveal
        secret={link?.url ?? null}
        title={LINKS[kind].title}
        description={
          link &&
          `No email is sent. Share this link; it works once and expires ${DATE_TIME.format(new Date(link.expiresAt))}. Older links stop working.`
        }
        copyLabel="Copy link"
        onClose={() => setLink(null)}
      />
    </div>
  );
}
