"use client";

import Link from "next/link";
import { KeyRound, LogOut, ShieldCheck } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { signOut } from "@/lib/auth";
import type { MeOut } from "@/lib/shell-data";
import { initials } from "@/lib/user";

async function signOutAndLeave() {
  try {
    await signOut();
  } finally {
    // A full load, so nothing of the signed-out user stays in client state.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/sign-in");
  }
}

/** The avatar (initials) and, from 768 px, the name; opens the account menu. */
export function UserMenu({ me }: { me: MeOut }) {
  const display = me.name || me.email || "your account";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            aria-label={`Account: ${display}`}
            className="min-w-11 shrink justify-start px-1.5 md:max-w-56"
          />
        }
      >
        <Avatar aria-hidden>
          <AvatarFallback>{initials(me.name, me.email)}</AvatarFallback>
        </Avatar>
        <span className="hidden truncate md:inline">{display}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto min-w-48">
        <DropdownMenuItem render={<Link href="/account/security" />}>
          <ShieldCheck aria-hidden />
          Profile &amp; security
        </DropdownMenuItem>
        <DropdownMenuItem render={<Link href="/account/tokens" />}>
          <KeyRound aria-hidden />
          API tokens
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => void signOutAndLeave()}>
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
