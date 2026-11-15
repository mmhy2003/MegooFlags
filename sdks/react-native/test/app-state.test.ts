import { describe, expect, it } from "vitest";
import { appStateSignal, type AppStateLike } from "../src/app-state.js";

class FakeAppState implements AppStateLike {
  currentState: string | null | undefined = "active";
  readonly listeners = new Set<(s: string) => void>();
  removed = 0;
  addEventListener(_type: "change", l: (s: string) => void) {
    this.listeners.add(l);
    return {
      remove: () => {
        this.removed++;
        this.listeners.delete(l);
      },
    };
  }
  emit(state: string) {
    this.currentState = state;
    for (const l of [...this.listeners]) l(state);
  }
}

describe("appStateSignal", () => {
  it.each([
    ["active", "visible"],
    ["background", "hidden"],
    ["inactive", "hidden"],
  ])("maps the current state %s to %s", (state, expected) => {
    const app = new FakeAppState();
    app.currentState = state;
    expect(appStateSignal(app).current()).toBe(expected);
  });

  it.each([["unknown"], ["extension"], [null], [undefined]])(
    "treats the current state %s as visible",
    (state) => {
      const app = new FakeAppState();
      app.currentState = state;
      expect(appStateSignal(app).current()).toBe("visible");
    },
  );

  it("tells the listener each change, never terminated", () => {
    const app = new FakeAppState();
    const seen: string[] = [];
    appStateSignal(app).subscribe((s) => seen.push(s));

    app.emit("inactive");
    app.emit("background");
    app.emit("active");

    expect(seen).toEqual(["hidden", "hidden", "visible"]);
  });

  it("unsubscribing removes the AppState subscription", () => {
    const app = new FakeAppState();
    const seen: string[] = [];
    const unsubscribe = appStateSignal(app).subscribe((s) => seen.push(s));

    unsubscribe();
    app.emit("background");

    expect(app.removed).toBe(1);
    expect(app.listeners.size).toBe(0);
    expect(seen).toEqual([]);
  });
});
