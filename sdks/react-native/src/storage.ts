// AsyncStorage, when the app installed it.
import type { StorageLike } from "@megooflags/js/platform";

declare const require: (id: string) => unknown;

/**
 * loadAsyncStorage returns @react-native-async-storage/async-storage, or
 * null when it is not installed. The literal require inside try/catch lets
 * Metro treat the package as optional.
 */
export function loadAsyncStorage(): StorageLike | null {
  try {
    const m = require("@react-native-async-storage/async-storage") as {
      default?: StorageLike;
    };
    return m.default ?? null;
  } catch {
    return null;
  }
}
