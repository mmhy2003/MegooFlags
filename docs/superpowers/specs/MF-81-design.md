# MF-81: Web UI foundation (app shell, cyberpunk neon theme, responsive layout, accessibility CI)

## Goal
Every signed-in page gets one shared app shell: a shadcn `Sidebar` on the left and a fixed header with ⌘K search, a theme switcher and a user menu. The app uses the PRD 6.8 cyberpunk neon palette in light, dark and system themes. It works from 280 px to wide desktops, and a kit of shared responsive components comes with it. A Playwright + axe matrix (6 widths × 2 themes) in CI keeps all of this in place. Mockup v1 (approved direction) shows the target look.

## Decisions
- **Preferences are stored on the user in the API and mirrored to cookies** (your answer). The setting follows the user across devices, and the server reads the cookies during render so nothing flashes. Rejected: cookie-only (lost on a new device) and localStorage-only (the sidebar flashes).
- **A new `GET /api/auth/me/permissions?project=<key>` endpoint** (your answer). The shell maps each menu item to a permission, and MF-82 pages can reuse the endpoint to hide actions. Rejected: org-level permissions inside `/me` (too coarse) and probing endpoints for 403s (flicker, many requests).
- **CI runs the real API with Postgres and Valkey**, seeded through `/api/setup` (your answer). Rejected: a mock API (drifts from the real one) and the Compose stack (slow and hard to debug).
- **Layout changes are CSS-only (Tailwind `md`/`lg`).** Components never mount different trees by JS width, except the shadcn mobile sidebar drawer. Folding a device therefore keeps form input, scroll position and open dialogs (UI-13).
- **Menu pages that don't exist yet are placeholder pages** with an empty state ("Coming soon"). MF-82 fills them in. **Nodes stays hidden** until multi-node support exists, because there is no API for it yet.

## Architecture (web)
- `app/(app)/layout.tsx` is a server layout. It calls `/me` and `/me/permissions` through a new `lib/api/server.ts`, which forwards the cookie and `x-forwarded-proto` the way `proxy.ts` does. It then renders `AppShell` with the user, the permitted nav items and the projects. Because `cacheComponents` is on, the dynamic `cookies()`/`headers()` reads sit inside a `<Suspense>` boundary.
- Routes:
  - `/` redirects to the last-used project (`mf_project` cookie) or the first visible one. With no projects, it shows an empty state.
  - Project pages live under `/projects/[project]/` as `flags`, `segments`, `environments`, `change-requests`, `audit`, `keys` and `integrations`, plus an org-level `/settings`.
  - `/account/security` moves into the shell, and `/account/tokens` is a placeholder.
  - Sign-in, MFA, setup and invite pages stay outside the shell.
- `components/shell/`:
  - `app-shell`: shadcn `SidebarProvider`, `Sidebar collapsible="icon"` and `SidebarInset`.
  - `project-switcher`.
  - `nav` (lucide icons, a route table, tooltips when collapsed, `aria-current` plus an accent bar and bold text, so the active item is never shown by colour alone).
  - `site-header` (sticky, safe-area padding):
    - below 768 px: ☰ menu button, logo icon, search icon and avatar only, with the palette opening full screen;
    - 768–1023 px: the sidebar forced to icons;
    - 1024 px and up: the full sidebar, which the user can collapse.
  - `command-palette`: shadcn `Command` in a dialog, opened with ⌘K/Ctrl+K. It searches:
    - permitted pages;
    - flags via `GET /projects/{p}/flags?q=` (debounced, limit 8);
    - segments and environments of the current project, filtered on the client;
    - a "switch theme" action.
  - `theme-switcher`: light, dark or system.
  - `user-menu`: avatar or initials, plus the name at `md` and up. It links to Profile/Security and API tokens, and has Sign out.
  - `brand`: an SVG logo in light and dark variants.
  - `http-warning`.
- **Nav → permission map** (a pure function in `lib/nav.ts`, unit tested):
  - Flags, Segments, Environments, Change requests: the project is visible (`project.read`).
  - Audit log: `audit.read`.
  - API & SDK keys: `sdk_key.read` in any environment.
  - Integrations: `project.manage`.
  - Settings: any of `org.manage`, `member.manage`, `role.manage` or `token.manage`.
- **Theming:**
  - `next-themes` (`attribute="class"`, `enableSystem`, blocking script, so no flash). The server passes the stored preference as `defaultTheme`.
  - After a change, the theme switcher calls `setTheme` and `PATCH`es the stored preference. On load, the shell applies the API value if it differs, which is how the choice syncs between devices.
  - Sidebar state uses shadcn's `sidebar_state` cookie, read during server render (falling back to `preferences.sidebar_collapsed`). Toggling it `PATCH`es the API.
- **Tokens:**
  - `globals.css` holds the PRD 6.8 table in `:root` (light) and `.dark`, in place of the neutral shadcn defaults.
  - New tokens: `--success`, `--warning`, their `-foreground` colours and `--glow`, all mapped in `@theme`. Values are tuned only to pass AA.
  - Glow is a `shadow-glow` utility used only on focus rings, primary buttons, active toggles and live dots. Pulse effects sit behind `motion-safe:`.
  - A Vitest check fails on any hex or `rgb(` value outside `globals.css`.
- **Favicon:** an SVG icon that switches colours with `prefers-color-scheme`.
- **Viewport and safe areas:** `viewport-fit=cover` and `interactive-widget=resizes-content`; `env(safe-area-inset-*)` on the header and the drawer; `scroll-padding-top` equal to the header height, so focused fields are never hidden behind the header.
- **Inputs:** always `text-base` (16 px).
- **Plain-HTTP warning (DEP-6):** `http-warning` lives in the **root** layout, so sign-in pages show it too. It is a server check: the scheme comes from `x-forwarded-proto`, else from the request, and the warning shows when the scheme is `http` and the host is not localhost or a loopback address. It uses `role="alert"`, an icon and text.
- **Local dev without Caddy:** a `rewrites()` fallback sends `/api/*` to `MEGOOFLAGS_API_URL`. Behind Caddy it has no effect, because Caddy routes `/api` itself.

### Shared component kit (`components/patterns/`)
- `status-pill`: icon, label and tone (ON/OFF/stale/killed/pending/error); never colour alone.
- `data-list`: a table at `md` and up (key columns plus an expandable row for the rest at `md`), stacked cards below `md` (key, status, main detail, a "More" `<details>`). One column definition drives both, and the switch is CSS-only.
- `responsive-dialog`: one dialog that becomes a full-screen sheet below `md`, through CSS only, so its content is never remounted. `confirm-dialog` (used later for the kill switch) is built on it.
- `toggle`: the shadcn `Switch` with a 44 px hit area and a required accessible label.
- `rollout-slider`: the shadcn `Slider` plus a numeric input (0–100, step 0.1), a touch-sized thumb and keyboard support.
- `json-viewer`: pretty-printed JSON in its own horizontally scrolling box, with a copy button.
- `diff-viewer`: a before/after line diff (the `diff` package) with +/− markers, not colour alone. It shows side by side at `lg` and stacked below.
- `empty-state` and `page-header`.
- A **kit gallery** at `/dev/kit` renders every component with sample data. It returns 404 unless `MEGOOFLAGS_UI_KIT=1`, and the e2e tests use it.

## Data and interfaces (API)
- **Migration `0013_user_preferences`:** adds `user.theme` (`light|dark|system`, default `system`) and `user.sidebar_collapsed` (bool, default false), with a working downgrade.
- **`MeOut`** gains `preferences: {theme, sidebar_collapsed}`; it is `null` for token principals.
- **`PATCH /api/account/preferences`:** partial update, returns the preferences, signed-in user sessions only. Validation errors return 422. It writes no audit entry, because these are personal UI settings.
- **`GET /api/auth/me/permissions?project=<key>`** returns `{org: string[], project: string[] | null, environments: {<env_key>: string[]}}`, computed by the existing `Authorizer`:
  - with no `project`, only `org` is returned;
  - a hidden or unknown project returns 404, as elsewhere;
  - personal tokens get their capped rights;
  - it is declared self-only in the route-permission test.
- After these changes, regenerate `openapi.json` and `schema.d.ts`, and add a CHANGELOG entry under Added.

## Error handling and edge cases
- If `/me/permissions` fails, the shell shows only the always-visible project items and logs the error. The page still renders, and the API keeps enforcing 403s.
- If the project fetch fails, the switcher shows an error state with a retry button.
- If `PATCH /api/account/preferences` fails, the change stays in the cookie and a toast says it was not saved.
- An unknown project in the URL returns the 404 page inside the shell.
- In the palette, empty results show "No results". Ctrl+K is not handled while focus is inside a code editor; this is reserved for later editors.

## Testing
- **API (pytest, TDD):**
  - preferences: defaults, PATCH, validation, token principal refused, migration down/up;
  - permissions endpoint: Admin, Editor, Viewer, project-scoped binding, personal token cap, hidden-project 404.
- **Web unit tests (Vitest, new):** the nav permission map, the HTTP-warning decision, trail and route helpers, and the no-hex check.
- **E2E (new `web-e2e` CI job):** Postgres and Valkey services, `alembic upgrade head`, the API, `pnpm build`, then `next start` with `MEGOOFLAGS_UI_KIT=1`. Playwright `globalSetup` creates the admin through `/api/setup`, adds a project with environments and flags, and saves the session state.
- **The matrix:** 280, 344, 390, 880, 1024 and 1440 px × light and dark (`colorScheme` plus the preference). For the shell pages and `/dev/kit`, it checks:
  - axe with the `wcag2a`, `wcag2aa`, `wcag21aa` tags, with zero violations (this includes contrast);
  - no horizontal scroll (`scrollWidth ≤ clientWidth`);
  - interactive elements at least 44×44 px;
  - `toHaveScreenshot` visual baselines. They are committed and generated inside the pinned Playwright Docker image, which the job also runs in.
- **Behaviour specs:**
  - the collapse state survives a reload and a new browser context (stored by the API);
  - the theme has no flash: the first paint's `html` class matches the stored preference;
  - ⌘K/Ctrl+K opens the palette and a flag result navigates;
  - the drawer opens below 768 px;
  - a Viewer doesn't see Settings or Integrations;
  - resizing from 344 to 880 px with text typed in an open dialog keeps the dialog and the text;
  - with `prefers-reduced-motion`, no animations run;
  - the HTTP banner appears for a non-loopback host such as `127.0.0.2`.
- The existing lint, format, typecheck and build steps keep passing.

## Out of scope
- Real content for the Flags, Segments, Environments, Change requests, Audit log, API & SDK keys, Integrations and Settings pages (MF-82).
- The Nodes page (multi-node work).
- Step-by-step long forms (built with the forms in MF-82).
- Docs-site and README badge theming.
- Keyboard shortcuts beyond ⌘K.
