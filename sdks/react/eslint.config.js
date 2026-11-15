import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores([
    "dist/**",
    "node_modules/**",
    "e2e/page/dist/**",
    "test-results/**",
    "playwright-report/**",
  ]),
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // The SDK runs in browsers and React Native: only tests may use Node.js modules.
    files: ["src/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["node:*"],
              message:
                "src/ runs outside Node.js: do not import node: modules.",
            },
          ],
        },
      ],
    },
  },
]);
