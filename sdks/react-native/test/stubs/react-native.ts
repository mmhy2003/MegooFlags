// The part of react-native the SDK uses, for tests in Node.

type AppStateStatus = "active" | "background" | "inactive";
type Listener = (state: AppStateStatus) => void;

const listeners = new Set<Listener>();

export const AppState = {
  currentState: "active" as AppStateStatus,
  addEventListener(_type: "change", listener: Listener) {
    listeners.add(listener);
    return { remove: () => listeners.delete(listener) };
  },
  /** Test helper: sets currentState and tells the listeners. */
  emit(state: AppStateStatus) {
    AppState.currentState = state;
    for (const listener of [...listeners]) listener(state);
  },
};
