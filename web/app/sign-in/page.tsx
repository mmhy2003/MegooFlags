"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  destinationFor,
  passkeySignIn,
  providers,
  retryAfter,
  signIn,
  type PublicProvider,
  type SignInOut,
} from "@/lib/auth";

const SSO_ERRORS: Record<string, string> = {
  sso_failed: "Single sign-on failed. Try again or use another method.",
  sso_email_conflict:
    "A local account already uses this email and was not linked. Sign in with your password, or ask an admin.",
  sso_user_disabled: "This account is disabled. Ask an admin to re-enable it.",
  sso_not_provisioned:
    "No account exists for you. Ask an admin to invite you first.",
};

const NOTICES: Record<string, string> = {
  password_set: "Your password is set. Sign in with it now.",
};

function failureMessage(response: Response, fallback: string): string {
  if (response.status === 401) return "Invalid email or password";
  if (response.status === 429) {
    const seconds = retryAfter(response);
    return seconds === null
      ? "Too many attempts, try again later"
      : `Too many attempts, try again in ${seconds} s`;
  }
  return fallback;
}

function SignInForm() {
  const params = useSearchParams();
  const next = params.get("next");
  const ssoError = params.get("error");
  const notice = NOTICES[params.get("notice") ?? ""] ?? null;
  const [error, setError] = useState<string | null>(
    ssoError ? (SSO_ERRORS[ssoError] ?? SSO_ERRORS.sso_failed) : null,
  );
  const [busy, setBusy] = useState(false);
  const [ssoProviders, setSsoProviders] = useState<PublicProvider[]>([]);

  useEffect(() => {
    providers()
      .then(({ data }) => setSsoProviders(data ?? []))
      .catch(() => setSsoProviders([]));
  }, []);

  function finish(out: SignInOut) {
    window.location.assign(destinationFor(out.status, next));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const { data, error, response } = await signIn(
        String(form.get("email")),
        String(form.get("password")),
      );
      if (data) return finish(data);
      setError(
        failureMessage(response, error?.message ?? "Sign-in failed, try again"),
      );
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  async function onPasskey() {
    setBusy(true);
    setError(null);
    try {
      const { data, error, response } = await passkeySignIn();
      if (data) return finish(data);
      setError(
        response.status === 401
          ? "That passkey was not accepted"
          : failureMessage(
              response,
              error?.message ?? "Passkey sign-in failed, try again",
            ),
      );
    } catch {
      setError("Passkey sign-in was cancelled");
    }
    setBusy(false);
  }

  function startUrl(provider: PublicProvider): string {
    return next
      ? `${provider.start_url}?${new URLSearchParams({ next })}`
      : provider.start_url;
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>
          <h1 className="text-xl font-semibold">Sign in to MegooFlags</h1>
        </CardTitle>
      </CardHeader>
      <form onSubmit={onSubmit}>
        <CardContent className="flex flex-col gap-4">
          {notice && !error && (
            <Alert>
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="username webauthn"
              required
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </div>
        </CardContent>
        <CardFooter className="mt-4 flex flex-col gap-2">
          <Button type="submit" className="w-full" disabled={busy}>
            Sign in
          </Button>
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={busy}
            onClick={onPasskey}
          >
            Sign in with a passkey
          </Button>
          {ssoProviders.map((provider) => (
            <Button
              key={provider.key}
              variant="outline"
              className="w-full"
              nativeButton={false}
              render={<a href={startUrl(provider)} />}
            >
              Continue with {provider.name}
            </Button>
          ))}
        </CardFooter>
      </form>
    </Card>
  );
}

export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <SignInForm />
      </Suspense>
    </main>
  );
}
