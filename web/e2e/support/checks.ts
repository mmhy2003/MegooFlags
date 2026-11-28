import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

/** UI-12: nothing wider than the viewport. */
export async function noHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth <=
      document.documentElement.clientWidth,
  );
}

/** WCAG 2.1 AA violations (contrast included) on the current page. */
export async function axeViolations(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  return result.violations;
}

const INTERACTIVE = [
  "a[href]",
  "button",
  "input:not([type=hidden])",
  "select",
  "textarea",
  "summary",
  ...[
    "button",
    "switch",
    "slider",
    "menuitem",
    "option",
    "tab",
    "checkbox",
  ].map((role) => `[role=${role}]`),
].join(", ");

/**
 * UI-16: visible interactive elements smaller than 44×44 px. An element counts its nearest
 * `[data-hit-area]` ancestor as its target; `[data-inline-link]` links in running text are exempt.
 */
export async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate((selector) => {
    const MIN = 43.5;
    return Array.from(document.querySelectorAll<HTMLElement>(selector)).flatMap(
      (el) => {
        if (el.closest("[data-inline-link], [aria-hidden=true], [inert]"))
          return [];
        if (!el.checkVisibility()) return [];
        const target = el.closest<HTMLElement>("[data-hit-area]") ?? el;
        const { width, height } = target.getBoundingClientRect();
        if (width >= MIN && height >= MIN) return [];
        const label =
          el.getAttribute("aria-label") ??
          el.textContent?.trim().slice(0, 40) ??
          "";
        return [
          `${el.tagName.toLowerCase()} "${label}" ${Math.round(width)}×${Math.round(height)}`,
        ];
      },
    );
  }, INTERACTIVE);
}
