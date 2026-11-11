"use client";

import { QRCodeSVG } from "qrcode.react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useState,
  type FormEvent,
} from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import {
  changePassword,
  confirmTotp,
  deletePasskey,
  enrolTotp,
  listPasskeys,
  listSessions,
  me,
  regenerateRecoveryCodes,
  registerPasskey,
  removeTotp,
  renamePasskey,
  revokeOtherSessions,
  revokeSession,
  signOut,
  type ErrorBody,
  type MeOut,
  type PasskeyOut,
  type SessionOut,
  type TotpEnrolment,
} from "@/lib/auth";

const MIN_PASSWORD = 12;

/** The message for a failed account request; a few codes get wording that says what to do. */
function failure(error: ErrorBody | undefined, fallback: string): string {
  switch (error?.code) {
    case "reauth_required":
      return "Sign in again with your second factor to do this";
    case "public_url_required":
      return "Passkeys need MEGOOFLAGS_PUBLIC_URL to be set";
    default:
      return error?.message ?? fallback;
  }
}

function hasFactor(user: MeOut): boolean {
  return Boolean(user.mfa?.totp) || (user.mfa?.passkeys ?? 0) > 0;
}

function formatTime(value: string | null): string {
  return value === null ? "never" : new Date(value).toLocaleString();
}

function ErrorAlert({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>
          <h2 className="text-lg font-semibold">{title}</h2>
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  );
}

function TotpSection({
  user,
  onChange,
  onCodes,
}: {
  user: MeOut;
  onChange: () => void;
  onCodes: (codes: string[]) => void;
}) {
  const [enrolment, setEnrolment] = useState<TotpEnrolment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  const onSetUp = () =>
    run(async () => {
      const { data, error } = await enrolTotp();
      if (data) setEnrolment(data);
      else setError(failure(error, "Could not start the setup, try again"));
    });

  const onConfirm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const code = String(new FormData(event.currentTarget).get("code")).trim();
    return run(async () => {
      const { data, error, response } = await confirmTotp(code);
      if (data) {
        if (data.recovery_codes) onCodes(data.recovery_codes);
        setEnrolment(null);
        onChange();
      } else {
        setError(
          response.status === 401
            ? "Invalid code"
            : failure(error, "Could not confirm the code, try again"),
        );
      }
    });
  };

  const onRemove = () =>
    run(async () => {
      const { error, response } = await removeTotp();
      if (response.status === 204) onChange();
      else setError(failure(error, "Could not remove the app, try again"));
    });

  return (
    <Section
      title="Authenticator app"
      description="Use a code from an app such as 1Password, Aegis or Google Authenticator."
    >
      <ErrorAlert error={error} />
      {user.mfa?.totp ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm">An authenticator app is set up.</p>
          <Button variant="outline" disabled={busy} onClick={onRemove}>
            Remove
          </Button>
        </div>
      ) : enrolment ? (
        <form onSubmit={onConfirm} className="flex flex-col gap-4">
          <p className="text-sm">
            Scan this code with your app, or enter the secret by hand.
          </p>
          <div className="w-fit rounded-lg bg-white p-3">
            <QRCodeSVG value={enrolment.otpauth_url} />
          </div>
          <code className="bg-muted rounded px-2 py-1 text-sm break-all">
            {enrolment.secret}
          </code>
          <div className="flex flex-col gap-2">
            <Label htmlFor="totp-code">6-digit code from the app</Label>
            <Input
              id="totp-code"
              name="code"
              autoComplete="one-time-code"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
            />
          </div>
          <Button type="submit" className="w-fit" disabled={busy}>
            Confirm
          </Button>
        </form>
      ) : (
        <Button className="w-fit" disabled={busy} onClick={onSetUp}>
          Set up
        </Button>
      )}
    </Section>
  );
}

function PasskeyRow({
  passkey,
  onChange,
  onError,
}: {
  passkey: PasskeyOut;
  onChange: () => void;
  onError: (error: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    onError(null);
    try {
      await action();
    } catch {
      onError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  const onRename = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name")).trim();
    return run(async () => {
      const { data, error } = await renamePasskey(passkey.id, name);
      if (data) {
        setEditing(false);
        onChange();
      } else onError(failure(error, "Could not rename the passkey, try again"));
    });
  };

  const onDelete = () => {
    if (!window.confirm(`Delete the passkey "${passkey.name}"?`)) return;
    return run(async () => {
      const { error, response } = await deletePasskey(passkey.id);
      if (response.status === 204) onChange();
      else onError(failure(error, "Could not delete the passkey, try again"));
    });
  };

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b py-2 last:border-b-0">
      {editing ? (
        <form onSubmit={onRename} className="flex flex-1 flex-wrap gap-2">
          <Input
            name="name"
            aria-label="Passkey name"
            defaultValue={passkey.name}
            maxLength={200}
            className="min-w-0 flex-1"
            required
          />
          <Button type="submit" size="sm" disabled={busy}>
            Save
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setEditing(false)}
          >
            Cancel
          </Button>
        </form>
      ) : (
        <>
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-medium">{passkey.name}</span>
            <span className="text-muted-foreground text-xs">
              Added {formatTime(passkey.created_at)}, last used{" "}
              {formatTime(passkey.last_used_at)}
            </span>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              Rename
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={onDelete}
            >
              Delete
            </Button>
          </div>
        </>
      )}
    </li>
  );
}

function PasskeysSection({
  onChange,
  onCodes,
}: {
  onChange: () => void;
  onCodes: (codes: string[]) => void;
}) {
  const [passkeys, setPasskeys] = useState<PasskeyOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [version, setVersion] = useState(0);

  useEffect(() => {
    listPasskeys()
      .then(({ data, error }) => {
        if (data) setPasskeys(data);
        else setError(failure(error, "Could not load your passkeys"));
      })
      .catch(() => setError("Cannot reach the server, try again"));
  }, [version]);

  function refresh() {
    setVersion((v) => v + 1);
    onChange();
  }

  async function onAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const name = String(new FormData(formElement).get("name")).trim();
    setBusy(true);
    setError(null);
    try {
      const { data, error } = await registerPasskey(name);
      if (data) {
        if (data.recovery_codes) onCodes(data.recovery_codes);
        formElement.reset();
        refresh();
      } else setError(failure(error, "Could not add the passkey, try again"));
    } catch {
      setError("Adding the passkey was cancelled");
    }
    setBusy(false);
  }

  return (
    <Section
      title="Passkeys"
      description="Sign in with your device's screen lock or a security key."
    >
      <ErrorAlert error={error} />
      {passkeys.length > 0 && (
        <ul className="flex flex-col">
          {passkeys.map((passkey) => (
            <PasskeyRow
              key={passkey.id}
              passkey={passkey}
              onChange={refresh}
              onError={setError}
            />
          ))}
        </ul>
      )}
      <form onSubmit={onAdd} className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Label htmlFor="passkey-name">Name</Label>
          <Input
            id="passkey-name"
            name="name"
            placeholder="Work laptop"
            maxLength={200}
            required
          />
        </div>
        <Button type="submit" disabled={busy}>
          Add a passkey
        </Button>
      </form>
    </Section>
  );
}

function RecoveryCodesSection({
  user,
  codes,
  onCodes,
  onChange,
}: {
  user: MeOut;
  codes: string[] | null;
  onCodes: (codes: string[]) => void;
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onRegenerate() {
    setBusy(true);
    setError(null);
    try {
      const { data, error } = await regenerateRecoveryCodes();
      if (data) {
        onCodes(data.recovery_codes);
        setCopied(false);
        onChange();
      } else setError(failure(error, "Could not create new codes, try again"));
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  async function onCopy() {
    if (!codes) return;
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
    } catch {
      setError("Cannot copy; select the codes and copy them by hand");
    }
  }

  return (
    <Section
      title="Recovery codes"
      description="Each code signs you in once if you lose your other factors."
    >
      <ErrorAlert error={error} />
      {codes && (
        <div className="flex flex-col gap-3">
          <Alert>
            <AlertDescription>
              Save these now; they won&apos;t be shown again.
            </AlertDescription>
          </Alert>
          <ul className="bg-muted grid grid-cols-2 gap-1 rounded-lg p-3 font-mono text-sm">
            {codes.map((code) => (
              <li key={code}>{code}</li>
            ))}
          </ul>
          <Button variant="outline" className="w-fit" onClick={onCopy}>
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          {user.mfa?.recovery_codes_left ?? 0} codes left.
        </p>
        {hasFactor(user) && (
          <Button variant="outline" disabled={busy} onClick={onRegenerate}>
            Regenerate
          </Button>
        )}
      </div>
    </Section>
  );
}

function PasswordSection({
  onSessionsChange,
}: {
  onSessionsChange: () => void;
}) {
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const next = String(form.get("new_password"));
    setNotice(null);
    if (next !== String(form.get("confirm_password"))) {
      setError("The new passwords do not match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { error, response } = await changePassword(
        String(form.get("current_password")),
        next,
      );
      if (response.status === 204) {
        formElement.reset();
        setNotice("Password changed. Your other sessions were signed out.");
        onSessionsChange();
      } else if (error?.code === "no_password") {
        setHidden(true);
      } else if (response.status === 401) {
        setError("The current password is wrong");
      } else {
        setError(failure(error, "Could not change the password, try again"));
      }
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  if (hidden) return null;
  return (
    <Section title="Password">
      <ErrorAlert error={error} />
      {notice && (
        <Alert>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="current_password">Current password</Label>
          <Input
            id="current_password"
            name="current_password"
            type="password"
            autoComplete="current-password"
            required
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="new_password">New password</Label>
          <Input
            id="new_password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={MIN_PASSWORD}
            required
          />
          <p className="text-muted-foreground text-xs">
            At least {MIN_PASSWORD} characters.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="confirm_password">Confirm new password</Label>
          <Input
            id="confirm_password"
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            minLength={MIN_PASSWORD}
            required
          />
        </div>
        <Button type="submit" className="w-fit" disabled={busy}>
          Change password
        </Button>
      </form>
    </Section>
  );
}

function SessionsSection({ version }: { version: number }) {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    listSessions()
      .then(({ data, error }) => {
        if (data) setSessions(data);
        else setError(failure(error, "Could not load your sessions"));
      })
      .catch(() => setError("Cannot reach the server, try again"));
  }, [version, reloads]);

  async function run(action: () => Promise<Response>) {
    setBusy(true);
    setError(null);
    try {
      const response = await action();
      if (response.status === 204) setReloads((n) => n + 1);
      else setError("Could not sign out the session, try again");
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  async function onRevoke(session: SessionOut) {
    if (session.current) {
      await revokeSession(session.id).catch(() => undefined);
      router.replace("/sign-in");
      return;
    }
    await run(async () => (await revokeSession(session.id)).response);
  }

  const others = sessions.some((session) => !session.current);

  return (
    <Section title="Sessions" description="Where you are signed in.">
      <ErrorAlert error={error} />
      <ul className="flex flex-col">
        {sessions.map((session) => (
          <li
            key={session.id}
            className="flex flex-wrap items-center justify-between gap-2 border-b py-2 last:border-b-0"
          >
            <div className="flex min-w-0 flex-col">
              <span className="truncate font-medium">
                {session.user_agent ?? "Unknown device"}
                {session.current && (
                  <span className="text-primary ml-2 text-xs font-semibold">
                    This session
                  </span>
                )}
              </span>
              <span className="text-muted-foreground text-xs">
                {session.ip ?? "Unknown IP"} · {session.auth_method} · last
                active {formatTime(session.last_seen_at)}
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onRevoke(session)}
            >
              Revoke
            </Button>
          </li>
        ))}
      </ul>
      {others && (
        <Button
          variant="outline"
          className="w-fit"
          disabled={busy}
          onClick={() =>
            run(async () => (await revokeOtherSessions()).response)
          }
        >
          Sign out other sessions
        </Button>
      )}
    </Section>
  );
}

function SecurityPage() {
  const params = useSearchParams();
  const router = useRouter();
  const [user, setUser] = useState<MeOut | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [sessionsVersion, setSessionsVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [userVersion, setUserVersion] = useState(0);
  const refresh = useCallback(() => setUserVersion((v) => v + 1), []);

  useEffect(() => {
    me()
      .then(({ data }) => {
        if (data === undefined) router.replace("/sign-in");
        else setUser(data);
      })
      .catch(() => setError("Cannot reach the server, try again"));
  }, [userVersion, router]);

  const enrolling =
    user !== null &&
    (user.session_state === "enrol_mfa" ||
      (params.get("enrol") === "1" && !hasFactor(user)));

  useEffect(() => {
    if (user !== null && !enrolling && params.get("enrol") !== null) {
      router.replace("/account/security");
    }
  }, [user, enrolling, params, router]);

  async function onSignOut() {
    try {
      await signOut();
    } finally {
      router.replace("/sign-in");
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Account security</h1>
        <Button variant="outline" onClick={onSignOut}>
          Sign out
        </Button>
      </div>
      <ErrorAlert error={error} />
      {user && enrolling && (
        <Alert>
          <AlertDescription>
            Your organisation requires two-factor authentication. Set up an
            authenticator app or a passkey to continue.
          </AlertDescription>
        </Alert>
      )}
      {user && (
        <>
          <TotpSection user={user} onChange={refresh} onCodes={setCodes} />
          <PasskeysSection onChange={refresh} onCodes={setCodes} />
        </>
      )}
      {user && !enrolling && (
        <>
          <RecoveryCodesSection
            user={user}
            codes={codes}
            onCodes={setCodes}
            onChange={refresh}
          />
          {!user.auth_method.startsWith("sso:") && (
            <PasswordSection
              onSessionsChange={() => setSessionsVersion((v) => v + 1)}
            />
          )}
          <SessionsSection version={sessionsVersion} />
        </>
      )}
    </main>
  );
}

export default function AccountSecurityPage() {
  return (
    <Suspense>
      <SecurityPage />
    </Suspense>
  );
}
