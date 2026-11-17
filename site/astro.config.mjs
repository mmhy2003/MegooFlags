// @ts-check
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightLinksValidator from "starlight-links-validator";

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
      plugins: [starlightLinksValidator()],
      sidebar: [],
    }),
  ],
});
