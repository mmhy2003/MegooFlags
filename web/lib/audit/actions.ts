import type { components } from "@/lib/api/schema";
import { changeRequestPath, flagPath } from "@/lib/routes";

type AuditEntry = components["schemas"]["AuditEntryOut"];

/** A readable label for each action the API records. */
const LABELS: Record<string, string> = {
  "account.passkey.delete": "Passkey removed",
  "account.passkey.register": "Passkey added",
  "account.passkey.rename": "Passkey renamed",
  "account.password.change": "Password changed",
  "account.recovery_codes.regenerate": "Recovery codes regenerated",
  "account.session.revoke": "Session signed out",
  "account.token.create": "Personal token created",
  "account.token.revoke": "Personal token revoked",
  "account.totp.confirm": "Authenticator app confirmed",
  "account.totp.enrol": "Authenticator app set up",
  "account.totp.remove": "Authenticator app removed",
  "auth.mfa.failed": "Two-factor check failed",
  "auth.setup": "First administrator set up",
  "auth.sign_in": "Signed in",
  "auth.sign_in.failed": "Sign-in failed",
  "auth.sign_out": "Signed out",
  "auth.sso.denied": "SSO sign-in denied",
  "auth.sso.sign_in": "Signed in with SSO",
  "binding.create": "Role assigned",
  "binding.delete": "Role removed",
  "change_request.apply": "Change request applied",
  "change_request.approve": "Change request approved",
  "change_request.cancel": "Change request cancelled",
  "change_request.comment": "Change request commented on",
  "change_request.create": "Change request opened",
  "change_request.fail": "Change request failed",
  "change_request.reject": "Change request rejected",
  "environment.create": "Environment created",
  "environment.delete": "Environment deleted",
  "environment.update": "Environment changed",
  "flag.archive": "Flag archived",
  "flag.config.update": "Targeting changed",
  "flag.copy": "Targeting copied",
  "flag.create": "Flag created",
  "flag.delete": "Flag deleted",
  "flag.kill": "Flag killed",
  "flag.lifecycle.update": "Flag lifecycle changed",
  "flag.restore": "Flag restored",
  "flag.rollout.abort": "Rollout aborted",
  "flag.rollout.cancel": "Rollout cancelled",
  "flag.rollout.complete": "Rollout completed",
  "flag.rollout.fail": "Rollout failed",
  "flag.rollout.pause": "Rollout paused",
  "flag.rollout.resume": "Rollout resumed",
  "flag.rollout.start": "Rollout started",
  "flag.rollout.step": "Rollout step",
  "flag.schedule.cancel": "Scheduled change cancelled",
  "flag.schedule.create": "Change scheduled",
  "flag.schedule.fail": "Scheduled change failed",
  "flag.schedule.run": "Scheduled change applied",
  "flag.toggle": "Flag turned on or off",
  "flag.update": "Flag changed",
  "flag.variation.create": "Variation added",
  "flag.variation.delete": "Variation deleted",
  "flag.variation.update": "Variation changed",
  "integration.create": "Integration created",
  "integration.delete": "Integration deleted",
  "integration.redeliver": "Integration delivery retried",
  "integration.rotate_secret": "Integration secret rotated",
  "integration.test": "Integration tested",
  "integration.update": "Integration changed",
  "invitation.accept": "Invitation accepted",
  "node.create": "Worker node added",
  "node.delete": "Worker node deleted",
  "node.drain": "Worker node drained",
  "node.resume": "Worker node resumed",
  "node.revoke": "Worker node revoked",
  "node.rotate": "Worker node token rotated",
  "project.create": "Project created",
  "project.delete": "Project deleted",
  "project.update": "Project changed",
  "role.create": "Role created",
  "role.delete": "Role deleted",
  "role.update": "Role changed",
  "sdk_key.create": "SDK key created",
  "sdk_key.revoke": "SDK key revoked",
  "sdk_key.rotate": "SDK key rotated",
  "segment.config.update": "Segment rules changed",
  "segment.create": "Segment created",
  "segment.delete": "Segment deleted",
  "segment.update": "Segment changed",
  "settings.update": "Organization settings changed",
  "sso.sync": "SSO groups synced",
  "sso_mapping.create": "SSO group mapping added",
  "sso_mapping.delete": "SSO group mapping removed",
  "sso_provider.create": "SSO provider created",
  "sso_provider.delete": "SSO provider deleted",
  "sso_provider.update": "SSO provider changed",
  "team.create": "Team created",
  "team.delete": "Team deleted",
  "team.member.add": "Team member added",
  "team.member.remove": "Team member removed",
  "team.update": "Team changed",
  "token.create": "Service token created",
  "token.revoke": "Service token revoked",
  "token.rotate": "Service token rotated",
  "user.create": "Member invited",
  "user.disable": "Member disabled",
  "user.enable": "Member enabled",
  "user.mfa_reset": "Member two-factor reset",
  "user.reset_link": "Member set-up link created",
  "user.update": "Member changed",
};

/** Every action the API records, sorted. */
export const AUDIT_ACTIONS: readonly string[] = Object.keys(LABELS).sort();

/** Every resource type the API records, sorted. */
export const RESOURCE_TYPES: readonly string[] = [
  "binding",
  "change_request",
  "environment",
  "flag",
  "integration",
  "node",
  "passkey",
  "project",
  "role",
  "sdk_key",
  "segment",
  "session",
  "settings",
  "sso_provider",
  "team",
  "token",
  "user",
  "variation",
];

/** "team.member.add" → "Team member add". */
function humanize(action: string): string {
  const words = action.replace(/[._]/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A readable label for `action`; unknown ones are humanized. */
export function actionLabel(action: string): string {
  return LABELS[action] ?? humanize(action);
}

/** The page of the entry's flag or change request; null for other resources or without a project. */
export function resourceHref(e: AuditEntry): string | null {
  if (!e.project) return null;
  if (e.resource_type === "flag") {
    const path = flagPath(e.project, e.resource_key);
    return e.environment
      ? `${path}?${new URLSearchParams({ env: e.environment }).toString()}`
      : path;
  }
  if (e.resource_type === "change_request") {
    return changeRequestPath(e.project, e.resource_key);
  }
  return null;
}

/** A before or after snapshot as JSON indented by 2 spaces; "" without one. */
export function snapshotText(v: Record<string, unknown> | null): string {
  return v === null ? "" : JSON.stringify(v, null, 2);
}
