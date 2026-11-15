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
  },
});
