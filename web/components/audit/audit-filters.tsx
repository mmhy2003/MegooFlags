"use client";

import { SlidersHorizontal } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import {
  useEnvironmentKeys,
  useProjectKeys,
} from "@/components/access/use-catalogue";
import { Button } from "@/components/ui/button";
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
import { AUDIT_ACTIONS, RESOURCE_TYPES } from "@/lib/audit/actions";
import {
  activeFilterCount,
  type AuditFilterValues,
  type AuditScope,
} from "@/lib/audit/filters";
import { must } from "@/lib/flags/errors";

const TEXT_DELAY_MS = 300;

type Patch = Partial<AuditFilterValues>;

/**
 * A text filter. It sends its text 300 ms after typing stops, and follows the URL when that
 * changes from outside (back and forward), but not when the URL only catches up with it.
 */
function TextField({
  label,
  value,
  placeholder,
  list,
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  list?: string;
  onChange: (text: string) => void;
}) {
  const id = useId();
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
  // Kept in a ref so a parent render doesn't restart the debounce.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if (text === sent) return;
    const timer = setTimeout(() => {
      setSent(text);
      onChangeRef.current(text.trim());
    }, TEXT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, sent]);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={text}
        placeholder={placeholder}
        list={list}
        autoComplete="off"
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[] | null;
  disabled?: boolean;
  onChange: (value: string) => void;
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
        <option value="">Any</option>
        {options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        {/* Keeps a URL's value selectable while the options load. */}
        {value && !options?.some((o) => o.value === value) && (
          <option value={value}>{value}</option>
        )}
      </NativeSelect>
    </div>
  );
}

function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (date: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/** The project's environments, for the project page. */
function ProjectEnvironment({
  project,
  value,
  onChange,
}: {
  project: string;
  value: string;
  onChange: (patch: Patch) => void;
}) {
  const environments = useApiData(`environments:${project}`, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project } },
      }),
    ),
  );
  return (
    <SelectField
      label="Environment"
      value={value}
      options={
        environments.data?.map((e) => ({ value: e.key, label: e.name })) ?? null
      }
      onChange={(env) => onChange({ env })}
    />
  );
}

/** A project, then one of its environments, for the organization's log. */
function OrgProjectEnvironment({
  filters,
  onChange,
}: {
  filters: AuditFilterValues;
  onChange: (patch: Patch) => void;
}) {
  const projects = useProjectKeys();
  const environments = useEnvironmentKeys(filters.project || null);
  const keyOptions = (keys: string[] | null) =>
    keys?.map((k) => ({ value: k, label: k })) ?? null;
  return (
    <>
      <SelectField
        label="Project"
        value={filters.project}
        options={keyOptions(projects)}
        onChange={(project) => onChange({ project, env: "" })}
      />
      <SelectField
        label="Environment"
        value={filters.env}
        options={keyOptions(environments)}
        disabled={!filters.project}
        onChange={(env) => onChange({ env })}
      />
    </>
  );
}

function Fields({
  scope,
  value,
  onChange,
}: {
  scope: AuditScope;
  value: AuditFilterValues;
  onChange: (patch: Patch) => void;
}) {
  const actionsId = useId();
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <TextField
        label="Action"
        value={value.action}
        placeholder="flag.update"
        list={actionsId}
        onChange={(action) => onChange({ action })}
      />
      <datalist id={actionsId}>
        {AUDIT_ACTIONS.map((a) => (
          <option key={a} value={a} />
        ))}
      </datalist>
      <TextField
        label="Actor"
        value={value.actor}
        placeholder="Email or token name"
        onChange={(actor) => onChange({ actor })}
      />
      <SelectField
        label="Resource type"
        value={value.resource_type}
        options={RESOURCE_TYPES.map((t) => ({ value: t, label: t }))}
        onChange={(resource_type) => onChange({ resource_type })}
      />
      <TextField
        label="Resource key"
        value={value.resource_key}
        onChange={(resource_key) => onChange({ resource_key })}
      />
      {scope.kind === "project" ? (
        <ProjectEnvironment
          project={scope.project}
          value={value.env}
          onChange={onChange}
        />
      ) : (
        <OrgProjectEnvironment filters={value} onChange={onChange} />
      )}
      <DateField
        label="From"
        value={value.from}
        onChange={(from) => onChange({ from })}
      />
      <DateField
        label="To"
        value={value.to}
        onChange={(to) => onChange({ to })}
      />
    </div>
  );
}

/**
 * The audit log's filters: inline from 768 px, and in a bottom sheet behind a "Filters (n)"
 * button below it, as the flag filters do.
 */
export function AuditFilters({
  scope,
  value,
  onChange,
}: {
  scope: AuditScope;
  value: AuditFilterValues;
  onChange: (patch: Patch) => void;
}) {
  const [open, setOpen] = useState(false);
  const wide = useMediaQuery("(min-width: 768px)", true);
  const fields = <Fields scope={scope} value={value} onChange={onChange} />;

  return (
    <div className="mb-4">
      <div className="hidden md:block">{fields}</div>
      <Button
        variant="outline"
        className="md:hidden"
        aria-expanded={open && !wide}
        onClick={() => setOpen(true)}
      >
        <SlidersHorizontal aria-hidden />
        Filters ({activeFilterCount(value, scope)})
      </Button>
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
