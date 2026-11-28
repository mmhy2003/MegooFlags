"use client";

import { useParams, useRouter } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { acceptInvitation, invitation, type InvitationOut } from "@/lib/auth";

const MIN_PASSWORD = 12;

type Load =
  | { state: "loading" }
  | { state: "invalid" }
  | { state: "error" }
  | { state: "ready"; invitation: InvitationOut };

function InviteForm() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    invitation(token)
      .then(({ data, response }) => {
        if (data) setLoad({ state: "ready", invitation: data });
        else setLoad({ state: response.status === 404 ? "invalid" : "error" });
      })
      .catch(() => setLoad({ state: "error" }));
  }, [token]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password"));
    if (password !== String(form.get("confirm"))) {
      setError("The passwords do not match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { error, response } = await acceptInvitation(token, password);
      if (response.status === 204) {
        router.replace("/sign-in?notice=password_set");
        return;
      }
      if (response.status === 404) {
        setLoad({ state: "invalid" });
      } else {
        setError(error?.message ?? "Could not set the password, try again");
      }
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  const reset = load.state === "ready" && load.invitation.purpose === "reset";

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>
          <h1 className="text-xl font-semibold">
            {reset ? "Reset your password" : "Join MegooFlags"}
          </h1>
        </CardTitle>
        {load.state === "ready" && (
          <CardDescription>
            Choose a password for {load.invitation.email}.
          </CardDescription>
        )}
      </CardHeader>
      {load.state === "invalid" && (
        <CardContent>
          <Alert variant="destructive">
            <AlertDescription>
              This link is invalid or has expired
            </AlertDescription>
          </Alert>
        </CardContent>
      )}
      {load.state === "error" && (
        <CardContent>
          <Alert variant="destructive">
            <AlertDescription>
              Cannot reach the server, try again
            </AlertDescription>
          </Alert>
        </CardContent>
      )}
      {load.state === "ready" && (
        <form onSubmit={onSubmit}>
          <CardContent className="flex flex-col gap-4">
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
                autoComplete="username"
                value={load.invitation.email}
                readOnly
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">New password</Label>
              <Input
                id="password"
                name="password"
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
              <Label htmlFor="confirm">Confirm password</Label>
              <Input
                id="confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
                minLength={MIN_PASSWORD}
                required
              />
            </div>
          </CardContent>
          <CardFooter className="mt-4">
            <Button type="submit" className="w-full" disabled={busy}>
              {reset ? "Reset password" : "Set password"}
            </Button>
          </CardFooter>
        </form>
      )}
    </Card>
  );
}

export default function InvitePage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <InviteForm />
      </Suspense>
    </main>
  );
}
