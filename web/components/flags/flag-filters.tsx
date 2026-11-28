"use client";

import { SlidersHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useApiData } from "@/hooks/use-api-data";
import { useMediaQuery } from "@/hooks/use-media-query";
import { api } from "@/lib/api/client";
import { must } from "@/lib/flags/errors";
import {
  activeFilterCount,
  type FlagFilters as Filters,
  type Lifecycle,
} from "@/lib/flags/filters";
import { parseTags } from "@/lib/flags/flag-form";

const LIFECYCLES: Lifecycle[] = [
  "new",
  "active",
  "permanent",
  "stale",
  "archived",
];

const SEARCH_DELAY_MS = 200;

/**
 * A text field's local value that follows `value` when it changes from outside (Clear filters,
 * back and forward), but not when it only catches up with what this field sent.
 */
function useFieldText(value: string) {
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  const [sent, setSent] = useState(value);
  if (value !== seen) {
    setSeen(value);
    if (value !== sent) {
      setText(value);
      setSent(value);
    }
  }
  return { text, setText, sent, setSent };
}

function SearchField({
  value,
  onChange,
}: {
  value: string;
  onChange: (q: string) => void;
}) {
  const id = useId();
  const { text, setText, sent, setSent } = useFieldText(value);
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
  }, [text, sent, setSent]);

  return (
    <div className="min-w-48 flex-1">
      <Label htmlFor={id} className="sr-only">
        Search flags
      </Label>
      <Input
        id={id}
        type="search"
        placeholder="Search flags"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}

/** A text field that commits on blur or Enter. */
function CommitField({
  label,
  value,
  placeholder,
  onCommit,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onCommit: (text: string) => void;
}) {
  const id = useId();
  const { text, setText, sent, setSent } = useFieldText(value);
  const commit = () => {
    if (text === sent) return;
    setSent(text);
    onCommit(text);
  };
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  disabled,
  onChange,
  children,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </NativeSelect>
    </div>
  );
}

function Fields({
  project,
  filters,
  onChange,
  onClear,
}: {
  project: string;
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  onClear: () => void;
}) {
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );
  const tagsText = filters.tags.join(", ");

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <CommitField
        label="Tags"
        value={tagsText}
        placeholder="web, checkout"
        onCommit={(text) => onChange({ tags: parseTags(text) })}
      />
      <CommitField
        label="Owner"
        value={filters.owner}
        onCommit={(text) => onChange({ owner: text.trim() })}
      />
      <SelectField
        label="Lifecycle"
        value={filters.lifecycle}
        onChange={(value) => onChange({ lifecycle: value as Lifecycle | "" })}
      >
        <option value="">Any</option>
        {LIFECYCLES.map((l) => (
          <option key={l} value={l}>
            {l}
          </option>
        ))}
      </SelectField>
      <SelectField
        label="Environment"
        value={filters.env}
        onChange={(env) =>
          onChange({ env, enabled: env ? filters.enabled : "" })
        }
      >
        <option value="">Any</option>
        {environments.data?.map((e) => (
          <option key={e.key} value={e.key}>
            {e.name}
          </option>
        ))}
        {/* Keeps a URL's environment selectable while the list loads. */}
        {filters.env &&
          !environments.data?.some((e) => e.key === filters.env) && (
            <option value={filters.env}>{filters.env}</option>
          )}
      </SelectField>
      <SelectField
        label="State in environment"
        value={filters.enabled}
        disabled={!filters.env}
        onChange={(value) => onChange({ enabled: value as Filters["enabled"] })}
      >
        <option value="">Any</option>
        <option value="on">On</option>
        <option value="off">Off</option>
      </SelectField>
      <div className="flex items-end">
        <Button variant="outline" onClick={onClear}>
          Clear filters
        </Button>
      </div>
    </div>
  );
}

/**
 * The flag list's search and filters. From 768 px the fields open below the search; on phones
 * the same button opens them in a bottom sheet. Both share one open state, so folding a device
 * keeps them open.
 */
export function FlagFilters({
  project,
  filters,
  onChange,
  onClear,
}: {
  project: string;
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wide = useMediaQuery("(min-width: 768px)", true);
  const fields = (
    <Fields
      project={project}
      filters={filters}
      onChange={onChange}
      onClear={onClear}
    />
  );

  return (
    <div className="mb-4 flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-2">
        <SearchField value={filters.q} onChange={(q) => onChange({ q })} />
        <Button
          variant="outline"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <SlidersHorizontal aria-hidden />
          Filters ({activeFilterCount(filters)})
        </Button>
      </div>
      <Collapsible open={open && wide} onOpenChange={setOpen}>
        <CollapsibleContent className="rounded-lg border p-4">
          {fields}
        </CollapsibleContent>
      </Collapsible>
      <Sheet open={open && !wide} onOpenChange={setOpen}>
        <SheetContent side="bottom" className="max-h-[90dvh] overflow-y-auto">
          <SheetHeader>
            <SheetTitle>Filters</SheetTitle>
          </SheetHeader>
          <div className="px-4 pb-4">{fields}</div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
