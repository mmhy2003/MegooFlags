// The <MegooFlagsProvider> component: registers a MegooFlagsWebProvider with
// OpenFeature for its lifetime and wraps the OpenFeature React provider.
import { MegooFlagsWebProvider, type MegooFlagsOptions } from "@megooflags/js";
import type { Platform } from "@megooflags/js/platform";
import { OpenFeatureProvider } from "@openfeature/react-sdk";
import {
  NOOP_PROVIDER,
  OpenFeature,
  type EvaluationContext,
} from "@openfeature/web-sdk";
import { useEffect, useRef, type ReactNode } from "react";
import type * as React from "react";

export interface MegooFlagsProviderProps {
  /** A client key (mf_client_…). */
  sdkKey: string;
  /** The MegooFlags API URL. */
  baseUrl?: string;
  /** The evaluation context; a change (compared by value) calls OpenFeature.setContext. */
  context?: EvaluationContext;
  /**
   * The other provider options. They are read when the provider is created,
   * that is on mount and when sdkKey, baseUrl or domain change; changing
   * them alone has no effect.
   */
  options?: Omit<MegooFlagsOptions, "sdkKey" | "baseUrl">;
  /** The OpenFeature domain to register the provider for; default: the default provider. */
  domain?: string;
  children?: ReactNode;
}

/** contextKey is the context as JSON with sorted object keys, so equal contexts compare equal. */
function contextKey(context: EvaluationContext | undefined): string {
  return JSON.stringify(context ?? {}, (_key, value: unknown) => {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      return value;
    }
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(value).sort()) {
      sorted[k] = (value as Record<string, unknown>)[k];
    }
    return sorted;
  });
}

/**
 * createMegooFlagsComponent returns the MegooFlagsProvider component, with
 * providers built on the platform the factory returns (default: the web).
 */
export function createMegooFlagsComponent(
  platform?: () => Platform,
): (props: MegooFlagsProviderProps) => React.JSX.Element {
  return function MegooFlagsProvider({
    sdkKey,
    baseUrl,
    context,
    options,
    domain,
    children,
  }: MegooFlagsProviderProps): React.JSX.Element {
    const key = contextKey(context);
    // The latest props, read by the effects without re-running them.
    const latest = useRef({ context, options, key });
    latest.current = { context, options, key };
    // The context key OpenFeature last received, or null before registration.
    const applied = useRef<string | null>(null);

    useEffect(() => {
      const { context: ctx, options: opts, key: k } = latest.current;
      const provider = new MegooFlagsWebProvider(
        {
          ...opts,
          sdkKey,
          ...(baseUrl === undefined ? {} : { baseUrl }),
        },
        platform?.(),
      );
      const initial = ctx ?? {};
      if (domain === undefined) {
        void OpenFeature.setProvider(provider, initial);
      } else {
        void OpenFeature.setProvider(domain, provider, initial);
      }
      applied.current = k;
      return () => {
        applied.current = null;
        if (domain === undefined) {
          void OpenFeature.setProvider(NOOP_PROVIDER);
        } else {
          void OpenFeature.setProvider(domain, NOOP_PROVIDER);
        }
        void provider.onClose();
      };
    }, [sdkKey, baseUrl, domain]);

    useEffect(() => {
      if (applied.current === null || applied.current === key) {
        return;
      }
      applied.current = key;
      const ctx = latest.current.context ?? {};
      if (domain === undefined) {
        void OpenFeature.setContext(ctx);
      } else {
        void OpenFeature.setContext(domain, ctx);
      }
    }, [key, domain]);

    return domain === undefined ? (
      <OpenFeatureProvider>{children}</OpenFeatureProvider>
    ) : (
      <OpenFeatureProvider domain={domain}>{children}</OpenFeatureProvider>
    );
  };
}

/**
 * MegooFlagsProvider registers a MegooFlags provider with OpenFeature while
 * it is mounted (never during server rendering) and makes the OpenFeature
 * hooks work in its children.
 */
export const MegooFlagsProvider = createMegooFlagsComponent();
