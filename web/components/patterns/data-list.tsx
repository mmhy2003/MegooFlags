"use client";

import type { ReactNode } from "react";

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * One column of a DataList. `priority` decides where it shows:
 * key and status lead each card; main shows everywhere; extra is a table
 * column from 1024 px and sits behind "More" below that.
 */
export type Column<T> = {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  priority: "key" | "status" | "main" | "extra";
};

function Extras<T>({ columns, row }: { columns: Column<T>[]; row: T }) {
  return (
    <details>
      <summary className="inline-flex min-h-11 min-w-11 cursor-pointer items-center text-sm font-medium">
        More
      </summary>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pb-2 text-sm">
        {columns.map((column) => (
          <div key={column.id} className="contents">
            <dt className="text-muted-foreground">{column.header}</dt>
            <dd className="min-w-0 break-words">{column.cell(row)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/**
 * A table from 768 px and stacked cards below it (UI-14). One set of columns
 * drives both, and the switch is CSS only, so folding a device keeps state.
 */
export function DataList<T>({
  columns,
  rows,
  rowKey,
  caption,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  caption: string;
}) {
  const extras = columns.filter((c) => c.priority === "extra");
  const lead = columns.filter(
    (c) => c.priority === "key" || c.priority === "status",
  );
  const main = columns.filter((c) => c.priority === "main");

  return (
    <>
      <Table className="hidden w-full md:table">
        <TableCaption className="sr-only">{caption}</TableCaption>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead
                key={column.id}
                className={
                  column.priority === "extra" ? "hidden lg:table-cell" : ""
                }
              >
                {column.header}
              </TableHead>
            ))}
            {extras.length > 0 && (
              <TableHead className="lg:hidden">
                <span className="sr-only">Details</span>
              </TableHead>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={rowKey(row)}>
              {columns.map((column) => (
                <TableCell
                  key={column.id}
                  className={
                    column.priority === "extra" ? "hidden lg:table-cell" : ""
                  }
                >
                  {column.cell(row)}
                </TableCell>
              ))}
              {extras.length > 0 && (
                <TableCell className="whitespace-normal lg:hidden">
                  <Extras columns={extras} row={row} />
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ul aria-label={caption} className="flex flex-col gap-3 md:hidden">
        {rows.map((row) => (
          <li key={rowKey(row)}>
            <article className="flex flex-col gap-1 rounded-lg border bg-card p-3 text-card-foreground">
              <div className="flex flex-wrap items-center justify-between gap-2">
                {lead.map((column) => (
                  <div key={column.id} className="min-w-0 break-words">
                    {column.cell(row)}
                  </div>
                ))}
              </div>
              {main.map((column) => (
                <div
                  key={column.id}
                  className="min-w-0 text-sm break-words text-muted-foreground"
                >
                  {column.cell(row)}
                </div>
              ))}
              {extras.length > 0 && <Extras columns={extras} row={row} />}
            </article>
          </li>
        ))}
      </ul>
    </>
  );
}
