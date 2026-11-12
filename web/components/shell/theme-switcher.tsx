"use client";

import { useSyncExternalStore } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { writeCookie } from "@/lib/cookie";
import { savePreferences } from "@/lib/preferences";
import {
  parseTheme,
  THEME_COOKIE,
  THEME_UNSAVED_COOKIE,
  type Theme,
} from "@/lib/theme";

const noop = () => () => {};

/** Applies a theme at once and keeps it in the cookie, then stores the choice on the user. If
 * that fails, the cookie marks it unsaved so the next load keeps it (`ThemeSync`). */
export function useApplyTheme(): (theme: Theme) => void {
  const { setTheme } = useTheme();
  return (theme) => {
    setTheme(theme);
    writeCookie(THEME_COOKIE, theme);
    void savePreferences({ theme }).then((saved) => {
      writeCookie(THEME_UNSAVED_COOKIE, saved ? null : "1");
      if (!saved)
        toast.error(
          "Couldn't save this preference. It applies in this browser only.",
        );
    });
  };
}

/** Light, dark or system. */
export function ThemeSwitcher({ className }: { className?: string }) {
  const { theme } = useTheme();
  const applyTheme = useApplyTheme();
  // The theme is only known in the browser; render the neutral icon on the server.
  const mounted = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
  const current = mounted ? parseTheme(theme) : null;
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;

  function choose(value: unknown) {
    const next = parseTheme(String(value));
    if (next) applyTheme(next);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label="Theme"
            className={className}
          />
        }
      >
        <Icon aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuRadioGroup value={current ?? ""} onValueChange={choose}>
          <DropdownMenuRadioItem value="light">
            <Sun aria-hidden />
            Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon aria-hidden />
            Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor aria-hidden />
            System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
