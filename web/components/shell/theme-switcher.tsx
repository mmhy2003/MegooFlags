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
import { savePreferences } from "@/lib/preferences";
import { parseTheme } from "@/lib/theme";

const noop = () => () => {};

/** Light, dark or system. Applies at once, then stores the choice on the user. */
export function ThemeSwitcher({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
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
    if (!next) return;
    setTheme(next);
    void savePreferences({ theme: next }).then((saved) => {
      if (!saved)
        toast.error(
          "Couldn't save this preference. It applies in this browser only.",
        );
    });
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
