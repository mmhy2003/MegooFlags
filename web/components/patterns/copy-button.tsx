"use client";

import { useEffect, useState } from "react";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";

type Outcome = "idle" | "copied" | "failed";

/** Copies text to the clipboard and says so in a polite live region. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [outcome, setOutcome] = useState<Outcome>("idle");

  useEffect(() => {
    if (outcome === "idle") return;
    const timer = setTimeout(() => setOutcome("idle"), 2000);
    return () => clearTimeout(timer);
  }, [outcome]);

  async function copy() {
    try {
      // The Clipboard API is missing on plain HTTP (DEP-6).
      await navigator.clipboard.writeText(text);
      setOutcome("copied");
    } catch {
      setOutcome("failed");
    }
  }

  return (
    <div className="flex items-center gap-2">
      <span aria-live="polite" className="text-sm text-muted-foreground">
        {outcome === "copied" && "Copied"}
        {outcome === "failed" && "Couldn't copy"}
      </span>
      <Button variant="outline" size="sm" onClick={() => void copy()}>
        <Copy aria-hidden />
        {label}
      </Button>
    </div>
  );
}
