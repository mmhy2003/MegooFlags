import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createVisibleInterval } from "@/hooks/use-visible-interval";

/** A stand-in for `document`: its visibility can be set, and it fires visibilitychange. */
function fakeDocument(state: DocumentVisibilityState) {
  const target = new EventTarget();
  return Object.assign(target, {
    visibilityState: state,
    show() {
      this.visibilityState = "visible";
      target.dispatchEvent(new Event("visibilitychange"));
    },
    hide() {
      this.visibilityState = "hidden";
      target.dispatchEvent(new Event("visibilitychange"));
    },
  });
}

describe("createVisibleInterval", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("calls back every interval while visible", () => {
    const callback = vi.fn();
    const stop = createVisibleInterval(callback, 1000, fakeDocument("visible"));
    vi.advanceTimersByTime(999);
    expect(callback).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(callback).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(callback).toHaveBeenCalledTimes(3);
    stop();
  });

  it("doesn't call back while hidden", () => {
    const callback = vi.fn();
    const doc = fakeDocument("visible");
    const stop = createVisibleInterval(callback, 1000, doc);
    doc.hide();
    vi.advanceTimersByTime(5000);
    expect(callback).not.toHaveBeenCalled();
    stop();
  });

  it("calls back once at once on becoming visible, then every interval", () => {
    const callback = vi.fn();
    const doc = fakeDocument("hidden");
    const stop = createVisibleInterval(callback, 1000, doc);
    vi.advanceTimersByTime(3000);
    expect(callback).not.toHaveBeenCalled();
    doc.show();
    expect(callback).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(callback).toHaveBeenCalledTimes(2);
    stop();
  });

  it("stops calling back once stopped", () => {
    const callback = vi.fn();
    const doc = fakeDocument("visible");
    const stop = createVisibleInterval(callback, 1000, doc);
    stop();
    doc.hide();
    doc.show();
    vi.advanceTimersByTime(5000);
    expect(callback).not.toHaveBeenCalled();
  });
});
