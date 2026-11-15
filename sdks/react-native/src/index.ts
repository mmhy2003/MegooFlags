import type { MegooFlagsProviderProps } from "@megooflags/react";
import { createMegooFlagsComponent } from "@megooflags/react/platform";
import type * as React from "react";
import { AppState } from "react-native";
import { reactNativePlatform } from "./platform.js";

export type { MegooFlagsProviderProps };

/**
 * MegooFlagsProvider registers a MegooFlags provider with OpenFeature while
 * it is mounted, on the React Native platform (XMLHttpRequest stream,
 * AsyncStorage, AppState), and makes the OpenFeature hooks work in its
 * children.
 */
export const MegooFlagsProvider: (
  props: MegooFlagsProviderProps,
) => React.JSX.Element = createMegooFlagsComponent(() =>
  reactNativePlatform({ appState: AppState }),
);
export { reactNativePlatform } from "./platform.js";
export { VERSION } from "./version.js";
export {
  OpenFeatureTestProvider,
  useBooleanFlagDetails,
  useBooleanFlagValue,
  useFlag,
  useNumberFlagDetails,
  useNumberFlagValue,
  useObjectFlagDetails,
  useObjectFlagValue,
  useOpenFeatureClient,
  useOpenFeatureClientStatus,
  useStringFlagDetails,
  useStringFlagValue,
  useSuspenseFlag,
  useWhenProviderReady,
} from "@megooflags/react";
