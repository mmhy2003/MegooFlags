"use client";

import { useState, type FormEvent } from "react";

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
import { setup } from "@/lib/auth";

const MIN_PASSWORD = 12;

export default function SetupPage() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const { error, response } = await setup({
        email: String(form.get("email")),
        name: String(form.get("name")),
        password: String(form.get("password")),
      });
      if (response.status === 201) {
        window.location.assign("/");
        return;
      }
      if (response.status === 409) {
        window.location.assign("/sign-in");
        return;
      }
      setError(error?.message ?? "Setup failed, try again");
    } catch {
      setError("Cannot reach the server, try again");
    }
    setBusy(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            <h1 className="text-xl font-semibold">Set up MegooFlags</h1>
          </CardTitle>
          <CardDescription>
            Create the first account. It becomes the organisation admin.
          </CardDescription>
        </CardHeader>
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
                autoComplete="email"
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="name">Name</Label>
              <Input id="name" name="name" autoComplete="name" required />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
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
          </CardContent>
          <CardFooter className="mt-4">
            <Button type="submit" className="w-full" disabled={busy}>
              Create admin account
            </Button>
          </CardFooter>
        </form>
      </Card>
    </main>
  );
}
