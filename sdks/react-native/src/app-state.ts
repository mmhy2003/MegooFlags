// The background signal of a React Native app.
import type {
  BackgroundSignal,
  BackgroundState,
} from "@megooflags/js/platform";

/** The part of React Native's AppState the SDK uses. */
export interface AppStateLike {
  currentState: string | null | undefined;
  addEventListener(
    type: "change",
    listener: (state: string) => void,
  ): { remove(): void };
}

/**
 * appStateSignal follows AppState: background and inactive are hidden;
 * active, and a state not known yet (null, unknown, extension), are
 * visible. An app is never reported terminated.
 */
export function appStateSignal(appState: AppStateLike): BackgroundSignal {
  const toState = (s: string | null | undefined): BackgroundState =>
    s === "background" || s === "inactive" ? "hidden" : "visible";
  return {
    current: () => toState(appState.currentState),
    subscribe(listener) {
      const sub = appState.addEventListener("change", (s) =>
        listener(toState(s)),
      );
      return () => sub.remove();
    },
  };
}
