"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { me, signOut, type MeOut } from "@/lib/auth";

export default function Home() {
  const [user, setUser] = useState<MeOut | null>(null);

  useEffect(() => {
    me()
      .then(({ data }) => setUser(data ?? null))
      .catch(() => setUser(null));
  }, []);

  async function onSignOut() {
    try {
      await signOut();
    } finally {
      window.location.assign("/sign-in");
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
      <h1 className="text-3xl font-semibold">MegooFlags</h1>
      {user && (
        <p className="text-muted-foreground">
          Signed in as {user.name ?? user.label}
        </p>
      )}
      <Button variant="outline" onClick={onSignOut}>
        Sign out
      </Button>
    </main>
  );
}
