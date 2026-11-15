// What the SDK uses from React Native.
import type { Logger } from "@megooflags/js";
import type { Platform, StorageLike } from "@megooflags/js/platform";
import { appStateSignal, type AppStateLike } from "./app-state.js";
import { loadAsyncStorage } from "./storage.js";
import { xhrStreamOpener } from "./xhr-stream.js";

let warned = false;

/**
 * reactNativePlatform is the React Native platform: the stream over
 * XMLHttpRequest, AsyncStorage (loaded when asyncStorage is undefined; null
 * means no cache) and AppState. Without a storage it warns once per process.
 */
export function reactNativePlatform(o: {
  appState: AppStateLike;
  asyncStorage?: StorageLike | null;
  Xhr?: typeof XMLHttpRequest;
  logger?: Logger;
}): Platform {
  const storage =
    o.asyncStorage === undefined ? loadAsyncStorage() : o.asyncStorage;
  if (storage === null && !warned) {
    warned = true;
    (o.logger ?? console).warn(
      "megooflags: @react-native-async-storage/async-storage is not installed; values are not cached across restarts",
    );
  }
  return {
    openStream: xhrStreamOpener(o.Xhr),
    storage,
    background: appStateSignal(o.appState),
  };
}
