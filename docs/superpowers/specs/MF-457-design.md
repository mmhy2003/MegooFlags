# MF-457: Web governance pages

## Goal
The five placeholder pages become working pages, so people can review change requests, read and export the audit log, manage SDK keys and tokens, and run the organization (members, teams, roles, SSO) from the web UI at every width from 280 px. This closes GA blocker 5 for these pages and the checklist rows UI-14 (audit log), UI-15, G-1, G-2, G-3, G-5 and the GA "Change requests" row.

## Decisions
Answers from the human:
- **Scope: everything in this task.** All five pages ship in one design and one pull request. *Rejected:* two or three phases with Settings as a follow-up.
- **Service tokens live in two places.** Settings gets a Service tokens tab with every token. The project's API & SDK keys page shows the tokens that can act in that project (org-wide ones are marked) and creates new ones pre-scoped to the project. Both use the same components. *Rejected:* only on the project page (no single list), only in Settings.
- **Organization audit entries get an Audit log tab in Settings.** The project page shows that project's entries; the Settings tab shows everything, for people with org-wide `audit.read`. *Rejected:* a scope switch on the project page, project entries only.
- **Small read-only API additions** fill the gaps (listed under "Data and interfaces"). *Rejected:* web-only workarounds (the web app would copy API rules and guess wrongly for SSO users and proxied URLs), and larger additions (invitations list with revoke, review filters, audit actions endpoint: a migration and more work before GA).
- **Layout: mockup v1.** A change request opens on its own page; Settings is one page with tabs kept in the URL; lists are tables that become cards under 768 px; forms and details open in dialogs that fill the screen on phones. *Rejected:* Settings as separate pages with a second-level menu.

Decisions made in the design:
- **Audit action filter:** a text field with suggestions from a fixed list in the web app; any text is accepted, and the API matches it exactly.
- **Viewer is read-only everywhere.** The comment box on a change request shows only to the author and to people who hold `flag.toggle`, `flag.config.write` or `change.approve` in that environment, although the API lets any reader comment.
- **Settings visibility:** the sidebar shows Settings to anyone with `org.manage`, `member.manage`, `role.manage`, `token.manage` or org-wide `audit.read`. Inside, each tab shows by permission; Teams and Roles are always there and are read-only without `member.manage` or `role.manage`.
- **Invite with a role:** the Invite dialog can assign one organization role. It calls `POST /users`, then `POST /bindings`. No email is sent; the link is shown once to copy.
- **Rotation overlap choices:** revoke now, 1 hour, 24 hours (default), 7 days, 30 days. **Token expiry choices:** never, 30 days, 90 days, 1 year, or a date. Personal tokens default to 90 days, service tokens to never.
- **Revoked and expired keys and tokens are hidden** until "Show revoked and expired" is ticked.

## Architecture (web)
All pages are client components fed by the generated `api` client, like the Integrations and Nodes pages. Filters, the selected tab and the selected environment live in URL search params, so a refresh, a fold or a rotation keeps them.

**Routes**
- `projects/[project]/change-requests/page.tsx`: the list.
- `projects/[project]/change-requests/[id]/page.tsx` (new): one request. `routes.ts` gains `changeRequestPath`.
- `projects/[project]/audit/page.tsx`: the project's audit log.
- `projects/[project]/keys/page.tsx`: SDK keys, then service tokens.
- `account/tokens/page.tsx`: personal tokens.
- `settings/page.tsx`: tabs with `?tab=`. An unknown or hidden tab falls back to the first visible one.

**Change requests** (`components/change-requests/`)
- `change-request-list`: `DataList` with title, flag, environment and kind, a status pill, "1 of 2" approvals, author and time. Filters: status (opens on Open), environment, flag key. "Load more" follows the cursor.
- `change-request-detail`: header with status and approvals; flag, environment, kind, author, description; the proposed change; reviews; comments with a comment box; the action bar.
  - **Proposed change:** a `config` request shows `DiffViewer` on `current` against `proposed`, with the same text form the flag page's review dialog uses. A toggle, schedule or rollout request shows a one-line summary built from `payload`, and loads the flag's environment config with the existing endpoint to show the current state.
  - **Stale warning:** shown on an open request when the flag's version is newer than `base_version`.
  - **Actions:** Approve (dialog with an optional comment; it says the change is applied at once when this is the last approval needed), Reject (dialog with a required reason), Cancel request (confirmation). Approve and Reject show when `can_approve` is true, Cancel when `can_cancel` is true. The bar is sticky at the bottom, with 44 px targets, so approve and reject work at 344 px (UI-15).

**Audit log** (`components/audit/`)
- `audit-log` takes `scope`: a project, or the organization. Both use the same filters, list and export.
- `audit-filters`: action, actor (email or token name), resource type, resource key, environment (project page) or project (Settings tab), from and to dates. Under 768 px they sit in a sheet, as the flag filters do.
- The list is a `DataList` (stacked cards under 768 px, UI-14) with time, a readable action label above the raw action, actor, resource and environment. A flag or change request links to its page. "Details" expands a row to show `DiffViewer` for before and after, and the sign-in method, IP address, user agent as recorded, and request ID.
- Export opens a dialog (CSV or NDJSON) and downloads `/api/audit/export` with the current filters through a plain link.

**Keys and tokens** (`components/keys/`, `components/tokens/`)
- `sdk-key-list`: environment tabs for the environments where the caller holds `sdk_key.read`. Each row shows name, kind, the key's first characters, a status (Active, Expires in …, Expired, Revoked) and the creation time. New SDK key (name, server or client with a short explanation), Rotate, Revoke, and Revoke now on a key that is expiring after a rotation. These need `sdk_key.manage` in that environment.
- `service-token-list` takes an optional `project`. Rows show name, first characters, status, access (role and scope), last used and expiry. New service token (name, expiry, one or more access rows), Access (add and remove bindings), Rotate, Revoke. Shown only with `token.manage`.
- `personal-token-list`: the same row shape plus scope and permissions. New token asks for name, where it works (everything, one project, one environment), permissions and expiry. The permission list holds only what the caller holds at that scope and what works at that level.
- `components/patterns/secret-reveal.tsx`: the Integrations dialog moved to the shared patterns with a title and a copy label, used for every one-time secret. It is an alert dialog, so only Done closes it.

**Settings** (`components/settings/`)
- **Members** (`member.manage`): search, status (Active, Invited with the link's expiry, Invite expired, Disabled), organization role, last sign-in. Invite member shows the link once. Manage opens a member: roles at any scope (roles from SSO are read-only), rename, a new invite or password-reset link, reset two-factor, disable or enable.
- **Teams:** list with member count and roles. Open shows members (added by hand or by SSO) and the team's roles. With `member.manage`: new team (key suggested from the name), rename, delete, add and remove members and roles.
- **Roles:** built-in and custom roles. The dialog groups permissions by level and says where each level works. Built-in roles, and any role without `role.manage`, open read-only. Create, edit and delete custom roles.
- **Service tokens** (`token.manage`): `service-token-list` without a project.
- **SSO** (`org.manage`): providers with protocol, status and what happens to new users. Add or edit a provider (OIDC or SAML fields; secrets are write-only and show "saved"), enable or disable, delete, Set-up URLs with copy buttons, and Group mappings (a group to a team, or to a role at a scope).
- **Security** (`org.manage`): the Require two-factor switch.
- **Audit log** (org-wide `audit.read`): `audit-log` with the organization scope.

**Shared**
- `components/access/`: `scope-picker` (organization, project, environment) and `access-editor` (a subject's bindings with add and remove). Members, teams, service tokens and SSO mappings use them.
- `lib/nav.ts`: Settings also shows with org-wide `audit.read`; API & SDK keys also shows with `token.manage`.
- Pure helpers, each with Vitest tests: `lib/change-requests/` (status pill, kind labels, summaries, stale check), `lib/audit/` (URL params to query, action labels, export URL), `lib/keys/status.ts`, `lib/tokens/expiry.ts`, `lib/access/` (permission labels, grouping by level, permissions grantable to a personal token, binding text), `lib/settings/tabs.ts` (visible tabs for a set of permissions).
- `PlaceholderPage` stays for Segments and Environments (MF-458); these five routes stop using it.

## Data and interfaces (API additions)
All are backward compatible and need no migration. Each regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` in the same commit and adds a CHANGELOG entry.
1. **`ChangeRequestOut` gains `can_approve` and `can_cancel`** for the caller. `can_approve`: the request is open, the caller holds `change.approve` in its environment, is not the author and has not reviewed it. `can_cancel`: the request is open and the caller is the author or holds `project.manage`.
2. **`GET /api/tokens` gains `project`** (a project key; unknown gives 404). It returns the tokens with a binding in that project or its environments, or at organization scope. The list is ordered newest first.
3. **New `GET /api/permissions`** for any signed-in caller: every permission with its level (`org`, `project`, `environment`), in catalogue order.
4. **`SsoProviderOut` gains `redirect_uri`** (OIDC) **and `acs_url`, `entity_id`, `metadata_url`** (SAML), built from the public URL as the sign-in flow builds them. They are null when `MEGOOFLAGS_PUBLIC_URL` is not set.
5. **`UserOut` gains `invitation_expires_at`:** the expiry of the user's unused invite link, also after it has passed; null once it is accepted. **`POST /users/{id}/reset-link`** issues an invite link, not a reset link, for a user who has no password and has never signed in, so the invite page keeps its "Join" wording.

## Error handling and edge cases
- **Approve can fail with HTTP 200.** When the flag changed, the request comes back as `failed`; the page shows the Failed state with the API's reason.
- **Someone else closed the request** (409 `invalid_state`): the page reloads it and says so.
- **Last admin** (409 `last_admin`) on disable, remove role, remove member, delete team or delete provider: "This would leave the organization without an admin."
- **Role in use** (409 `in_use`): the message lists where the role is used, from `details.used_by`.
- **Granting more than you hold** (403) and **`exceeds_owner_rights`** (422): the API's message is shown, with the refused permissions.
- **Invite created but role refused:** the link is still shown, with a warning that the role was not assigned.
- **Duplicate key or email** (409 `key_conflict`) and validation errors (422) show on the form field.
- **Rotating a revoked or expired key** (409 `key_inactive`): the list reloads.
- **No public URL:** the Set-up URLs dialog explains how to set `MEGOOFLAGS_PUBLIC_URL`.
- **A SAML metadata URL that cannot be fetched** shows on that field.
- Every list has a loading skeleton, an error with Try again, and an empty state. An unknown change request shows the not-found page.
- `failureText` in `lib/flags/errors.ts` gains the new codes.

## Testing
- **API (pytest, written first):** each of the five additions, including permissions (`can_approve` for the author, a reviewer and a viewer; the token `project` filter with org-wide tokens; invite versus reset links), plus the route permission and OpenAPI tests.
- **Web unit (Vitest):** every helper listed above.
- **Playwright specs,** against the real API:
  - `change-requests.spec.ts`: filters, the diff, comment, approve applies the change, reject needs a reason, cancel by the author, a stale request fails, a Viewer sees no actions, and approve and reject at 344 px.
  - `audit.spec.ts`: each filter, details with the diff, CSV and NDJSON downloads, cards under 768 px, and the organization tab.
  - `keys.spec.ts`: create a server and a client key with the secret shown once, rotate with an overlap, revoke, Viewer read-only; create a service token from the project, change its access, rotate, revoke.
  - `account-tokens.spec.ts`: create a scoped token, the permission list is limited, revoke.
  - `settings.spec.ts`: invite, open the link and set a password, assign and remove roles, disable and enable, the last-admin message; team with members and a role; custom role create, edit, delete and the in-use message; an OIDC provider with set-up URLs, a mapping, disable and delete; Viewer sees only Teams, Roles and Audit log. The Require two-factor switch is tested with an intercepted request, because turning it on would lock out the other specs' users.
- **Matrix (`matrix.spec.ts`):** change requests, a change request, audit log, keys, personal tokens and the seven Settings tabs, at 280, 344, 390, 880, 1024 and 1440 px in both themes. `global-setup.ts` seeds a `gov` project that no spec changes (an approval environment, change requests, SDK keys, a service token, a team, a custom role, a disabled SSO provider). Random text (key prefixes, ids, times) is masked, and the Members and organization Audit tabs are pinned with URL filters. Baselines come from `pnpm e2e:docker --update-snapshots`.
- **Final checks:** the web and API commands in AGENTS.md section 9, including the OpenAPI drift check.

## Documentation
- `docs/ga-checklist.md`: UI-14 (audit log part; Segments stays with MF-458), UI-15, G-1, G-2, G-3, G-5 and the GA "Change requests" row become done, with the new specs as evidence; blocker 5 is narrowed to Segments and Environments.
- `CHANGELOG.md`: entries under `[Unreleased]` for the pages and the API additions.

## Out of scope
- Segments, Environments, project settings and the scheduling form (MF-458).
- Editing an open change request, reopening or retrying one, a "needs my review" filter and an open-request count.
- An invitations list, revoking an invite link, deleting users, and sending invitation emails.
- Renaming keys and tokens, changing a token's expiry, and rotating personal tokens (the API has none of these).
- An SSO test-connection check, and an option to turn off password sign-in.
- Free-text or prefix search in the audit log, and an API list of audit actions.
- Rewriting the docs site quick start to use the UI in place of curl (`scripts/quickstart_test.sh` follows the documented steps).