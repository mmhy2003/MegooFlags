// @ts-check
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightLinksValidator from "starlight-links-validator";
import starlightOpenAPI, { openAPISidebarGroups } from "starlight-openapi";

export default defineConfig({
  site: "https://mmhy2003.github.io",
  base: "/MegooFlags",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "MegooFlags",
      logo: {
        light: "./src/assets/logo-light.svg",
        dark: "./src/assets/logo-dark.svg",
        replacesTitle: true,
      },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/mmhy2003/MegooFlags",
        },
      ],
      editLink: {
        baseUrl: "https://github.com/mmhy2003/MegooFlags/edit/main/site/",
      },
      customCss: [
        "@fontsource-variable/geist",
        "@fontsource-variable/geist-mono",
        "./src/styles/brand.css",
      ],
      plugins: [
        starlightOpenAPI([
          {
            base: "reference/api",
            label: "REST API",
            schema: "../api/openapi.json",
          },
        ]),
        starlightLinksValidator(),
      ],
      sidebar: [
        {
          label: "Start here",
          items: ["start/introduction", "start/quick-start", "start/concepts"],
        },
        {
          label: "Deploy",
          items: [
            "deploy/single-node",
            "deploy/tls",
            "deploy/reverse-proxy",
            "deploy/multi-node",
            "deploy/multi-node-migration",
            "deploy/upgrades",
            "deploy/configuration",
            "deploy/observability",
            "deploy/security",
          ],
        },
        {
          label: "Use",
          items: [
            "use/release-workflows",
            "use/flags-as-code",
            "use/integrations",
            "use/cli",
            "use/terraform",
          ],
        },
        {
          label: "SDKs",
          items: [
            "sdks/go",
            "sdks/node",
            "sdks/python",
            "sdks/java",
            "sdks/dotnet",
            "sdks/ruby",
            "sdks/php",
            "sdks/js",
            "sdks/react",
            "sdks/react-native",
            "sdks/ios",
            "sdks/android",
            "sdks/flutter",
          ],
        },
        {
          label: "Reference",
          items: [
            ...openAPISidebarGroups,
            "reference/evaluation",
            "reference/delivery",
          ],
        },
        { label: "Migrate", items: ["migrate/overview"] },
        {
          label: "Project",
          items: [
            "project/roadmap",
            "project/contributing",
            "project/rfcs",
            "project/code-of-conduct",
            "project/security-policy",
            "project/privacy",
            "project/release-notes",
          ],
        },
      ],
    }),
  ],
});
