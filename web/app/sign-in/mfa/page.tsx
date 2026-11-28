"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  codeFailure,
  me,
  mfaRecovery,
  mfaTotp,
  passkeySignIn,
  type MeOut,
} from "@/lib/auth";

type Method = "totp" | "passkey" | "recovery";

function methodsOf(user: MeOut): Method[] {
  const methods: Method[] = [];
  if (user.mfa?.totp) methods.push("totp");
  if (user.mfa && user.mfa.passkeys > 0) methods.push("passkey");
  if (user.mfa && user.mfa.recovery_codes_left > 0) methods.push("recovery");
  return methods;
}

function CodeForm({
  label,
  autoComplete,
  inputMode,
  pattern,
  maxLength,
  submit,
}: {
  label: string;
  autoComplete?: string;
  inputMode?: "numeric" | "text";
  pattern?: string;
  maxLength?: number;
  submit: (code: string) => Promise<{ response: Response; data?: unknown }>;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = String(new FormData(event.currentTarget).get("code")).trim();
    setBusy(true);
    setError(null);
    try {
      const { data, response } = await submit(code);
      if (data) {
        router.replace("/");
        return;
      }
      setError(codeFailure(response, "Verification failed, try again"));
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4 pt-2">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col gap-2">
        <Label htmlFor={`code-${label}`}>{label}</Label>
        <Input
          id={`code-${label}`}
          name="code"
          autoComplete={autoComplete ?? "off"}
          inputMode={inputMode}
          pattern={pattern}
          maxLength={maxLength}
          autoFocus
          required
        />
      </div>
      <Button type="submit" className="w-full" disabled={busy}>
        Verify
      </Button>
    </form>
  );
}

function PasskeyPanel() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onPasskey() {
    setBusy(true);
    setError(null);
    try {
      const { data, response } = await passkeySignIn();
      if (data) {
        router.replace("/");
        return;
      }
      setError(
        response.status === 401
          ? "That passkey was not accepted"
          : codeFailure(response, "Passkey verification failed, try again"),
      );
    } catch {
      setError("Passkey verification was cancelled");
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-4 pt-2">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Button className="w-full" disabled={busy} onClick={onPasskey}>
        Use a passkey
      </Button>
    </div>
  );
}

const LABELS: Record<Method, string> = {
  totp: "Authenticator app",
  passkey: "Passkey",
  recovery: "Recovery code",
};

export default function MfaPage() {
  const router = useRouter();
  const [methods, setMethods] = useState<Method[] | null>(null);

  useEffect(() => {
    me()
      .then(({ data }) => {
        if (data === undefined) {
          router.replace("/sign-in");
          return;
        }
        if (data.session_state !== "pending_mfa") {
          router.replace("/");
          return;
        }
        setMethods(methodsOf(data));
      })
      .catch(() => setMethods([]));
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl font-semibold">Two-factor authentication</h1>
          </CardTitle>
          <CardDescription>
            Confirm it is you with your second factor.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {methods !== null && methods.length === 0 && (
            <Alert variant="destructive">
              <AlertDescription>
                Cannot load your sign-in methods.{" "}
                <Link className="underline" href="/sign-in">
                  Start again
                </Link>
              </AlertDescription>
            </Alert>
          )}
          {methods !== null && methods.length > 0 && (
            <Tabs defaultValue={methods[0]}>
              <TabsList className="w-full">
                {methods.map((method) => (
                  <TabsTrigger key={method} value={method}>
                    {LABELS[method]}
                  </TabsTrigger>
                ))}
              </TabsList>
              <TabsContent value="totp">
                <CodeForm
                  label="6-digit code"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  submit={mfaTotp}
                />
              </TabsContent>
              <TabsContent value="passkey">
                <PasskeyPanel />
              </TabsContent>
              <TabsContent value="recovery">
                <CodeForm
                  label="Recovery code"
                  maxLength={64}
                  submit={mfaRecovery}
                />
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
