// The browser bundle of the end-to-end page: a React root with
// <MegooFlagsProvider> around a <Flag/> that renders e2e-flag as data-value
// and data-reason, driven through window.megooReact, and a logger that
// captures every line in window.megooLogs.
import { createRoot, type Root } from "react-dom/client";
import type { Logger } from "@megooflags/js";
import { MegooFlagsProvider, useBooleanFlagDetails } from "../../src/index.js";

/** The context the tests set: a targeting key (plain data, so page.evaluate can pass it). */
export type E2EContext = { targetingKey: string };

export interface MountProps {
  sdkKey: string;
  baseUrl: string;
  context: E2EContext;
  initTimeoutMs?: number;
}

export interface MegooReactWindow {
  megooReact: {
    /** Renders the app into a new root (unmounting the previous one). */
    mount(props: MountProps): void;
    /** Re-renders the app with a new context prop. */
    setContext(context: E2EContext): void;
    /** Unmounts the app, which closes the provider. */
    unmount(): void;
  };
  megooLogs: string[];
}

const logs: string[] = [];
const capture =
  (level: string) =>
  (message: string, ...args: unknown[]) => {
    logs.push([`${level}:`, message, ...args.map(String)].join(" "));
  };
const logger: Logger = {
  debug: capture("debug"),
  info: capture("info"),
  warn: capture("warn"),
  error: capture("error"),
};

function Flag() {
  const details = useBooleanFlagDetails("e2e-flag", true);
  return (
    <p
      data-testid="flag"
      data-value={String(details.value)}
      data-reason={details.reason ?? ""}
    >
      e2e-flag: {String(details.value)}
    </p>
  );
}

function App({ sdkKey, baseUrl, context, initTimeoutMs }: MountProps) {
  return (
    <MegooFlagsProvider
      sdkKey={sdkKey}
      baseUrl={baseUrl}
      context={context}
      options={{
        flushIntervalMs: 10_000,
        logger,
        ...(initTimeoutMs === undefined ? {} : { initTimeoutMs }),
      }}
    >
      <Flag />
    </MegooFlagsProvider>
  );
}

let root: Root | null = null;
let current: MountProps | null = null;

const w = window as unknown as MegooReactWindow;
w.megooLogs = logs;
w.megooReact = {
  mount(props) {
    root?.unmount();
    const el = document.getElementById("root");
    if (el === null) {
      throw new Error("no #root element");
    }
    root = createRoot(el);
    current = props;
    root.render(<App {...props} />);
  },
  setContext(context) {
    if (root === null || current === null) {
      throw new Error("not mounted");
    }
    current = { ...current, context };
    root.render(<App {...current} />);
  },
  unmount() {
    root?.unmount();
    root = null;
    current = null;
  },
};
