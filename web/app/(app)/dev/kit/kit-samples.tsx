"use client";

import { useState } from "react";

import { DataList, type Column } from "@/components/patterns/data-list";
import { RolloutSlider } from "@/components/patterns/rollout-slider";
import { StatusPill, type Status } from "@/components/patterns/status-pill";
import { Toggle } from "@/components/patterns/toggle";

type SampleFlag = {
  key: string;
  status: Status;
  summary: string;
  updated: string;
  owner: string;
};

const FLAGS: SampleFlag[] = [
  {
    key: "new-checkout",
    status: "on",
    summary: "25 % rollout · 2 rules",
    updated: "2 hours ago",
    owner: "Ada Admin",
  },
  {
    key: "dark-mode",
    status: "off",
    summary: "Serves false to everyone",
    updated: "Yesterday",
    owner: "Vic Viewer",
  },
  {
    key: "pricing-copy",
    status: "stale",
    summary: "No evaluations in 30 days",
    updated: "3 months ago",
    owner: "Pat Prefs",
  },
];

const COLUMNS: Column<SampleFlag>[] = [
  {
    id: "key",
    header: "Key",
    priority: "key",
    cell: (flag) => <span className="font-mono break-all">{flag.key}</span>,
  },
  {
    id: "status",
    header: "Status",
    priority: "status",
    cell: (flag) => <StatusPill status={flag.status} />,
  },
  {
    id: "summary",
    header: "Targeting",
    priority: "main",
    cell: (flag) => flag.summary,
  },
  {
    id: "updated",
    header: "Updated",
    priority: "extra",
    cell: (flag) => flag.updated,
  },
  {
    id: "owner",
    header: "Owner",
    priority: "extra",
    cell: (flag) => flag.owner,
  },
];

export function DataListSample() {
  return (
    <DataList
      columns={COLUMNS}
      rows={FLAGS}
      rowKey={(flag) => flag.key}
      caption="Sample flags"
    />
  );
}

export function ToggleSample() {
  const [on, setOn] = useState(true);
  return (
    <div className="flex flex-wrap gap-4">
      <Toggle
        checked={on}
        onCheckedChange={setOn}
        label="new-checkout in production"
      />
      <Toggle
        checked={false}
        onCheckedChange={() => {}}
        label="dark-mode in production (disabled)"
        disabled
      />
    </div>
  );
}

export function RolloutSample() {
  const [value, setValue] = useState(25);
  return (
    <RolloutSlider
      value={value}
      onValueChange={setValue}
      label="new-checkout rollout"
    />
  );
}
