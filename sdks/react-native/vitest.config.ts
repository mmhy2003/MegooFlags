import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // react-native ships Flow sources that Node cannot load: tests use a stub.
    alias: {
      "react-native": fileURLToPath(
        new URL("./test/stubs/react-native.ts", import.meta.url),
      ),
    },
    // The linked @megooflags packages have their own node_modules: one copy
    // of React and OpenFeature for the whole test.
    dedupe: [
      "react",
      "react-dom",
      "@openfeature/react-sdk",
      "@openfeature/web-sdk",
      "@openfeature/core",
    ],
  },
});
