"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const SEARCH_DELAY_MS = 200;

/** The search box; it sends its text after a short pause, and follows `value` (back, forward). */
export function SearchField({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (q: string) => void;
}) {
  const id = useId();
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  // A new `value` replaces the text, unless it only catches up with what this field sent.
  if (value !== seen) {
    setSeen(value);
    if (value !== sent) {
      setText(value);
      setSent(value);
    }
  }
  // Kept in a ref so a parent render doesn't restart the debounce.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  useEffect(() => {
    if (text === sent) return;
    const timer = setTimeout(() => {
      setSent(text);
      onChangeRef.current(text);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, sent]);

  return (
    <div className="min-w-48 flex-1 sm:max-w-sm">
      <Label htmlFor={id} className="sr-only">
        {label}
      </Label>
      <Input
        id={id}
        type="search"
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}
