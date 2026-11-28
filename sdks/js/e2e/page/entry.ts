// The browser bundle of the end-to-end page: puts the OpenFeature web SDK and
// the provider on window.megoo, and a logger that captures every line in
// window.megooLogs.
import { OpenFeature, ProviderEvents } from "@openfeature/web-sdk";
import { MegooFlagsWebProvider, type Logger } from "../../src/index.js";

export interface MegooWindow {
  megoo: {
    OpenFeature: typeof OpenFeature;
    ProviderEvents: typeof ProviderEvents;
    MegooFlagsWebProvider: typeof MegooFlagsWebProvider;
    logger: Logger;
  };
  megooLogs: string[];
  /** Whatever a test keeps between page.evaluate calls. */
  megooState: Record<string, unknown>;
}

const logs: string[] = [];
const capture =
  (level: string) =>
  (message: string, ...args: unknown[]) => {
    logs.push([`${level}:`, message, ...args.map(String)].join(" "));
  };

const w = window as unknown as MegooWindow;
w.megooLogs = logs;
w.megooState = {};
w.megoo = {
  OpenFeature,
  ProviderEvents,
  MegooFlagsWebProvider,
  logger: {
    debug: capture("debug"),
    info: capture("info"),
    warn: capture("warn"),
    error: capture("error"),
  },
};
