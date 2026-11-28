import { CopyButton } from "@/components/patterns/copy-button";

/** Pretty-printed JSON that scrolls inside its own box (UI-18), with a copy button. */
export function JsonViewer({
  value,
  label,
}: {
  value: unknown;
  label?: string;
}) {
  const json = JSON.stringify(value, null, 2);
  return (
    <div
      data-testid="json-viewer"
      className="relative flex min-w-0 flex-col gap-2"
    >
      <div className="flex justify-end">
        <CopyButton text={json} label="Copy JSON" />
      </div>
      <pre
        tabIndex={0}
        role="region"
        aria-label={label ?? "JSON"}
        className="max-w-full overflow-x-auto rounded-lg border bg-muted p-3 font-mono text-sm"
      >
        {json}
      </pre>
    </div>
  );
}
