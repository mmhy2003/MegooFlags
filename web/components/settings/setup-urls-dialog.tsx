"use client";

import { CopyButton } from "@/components/patterns/copy-button";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { components } from "@/lib/api/schema";

type Provider = components["schemas"]["SsoProviderOut"];

const URLS = [
  { field: "redirect_uri", label: "Redirect URI", copy: "Copy redirect URI" },
  { field: "acs_url", label: "ACS URL", copy: "Copy ACS URL" },
  { field: "entity_id", label: "Entity ID", copy: "Copy entity ID" },
  { field: "metadata_url", label: "Metadata URL", copy: "Copy metadata URL" },
] as const;

/**
 * The URLs an identity provider's admin enters for this provider: the redirect URI for OIDC, the
 * ACS URL, entity ID and metadata URL for SAML. The API sends none without a public URL.
 */
export function SetupUrlsDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: Provider | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const urls = URLS.flatMap((u) => {
    const value = provider?.[u.field];
    return value ? [{ ...u, value }] : [];
  });
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Set-up URLs"
      description={
        provider &&
        `Enter these at your identity provider for ${provider.name}.`
      }
    >
      {urls.length === 0 ? (
        <Alert>
          <AlertDescription>
            Set MEGOOFLAGS_PUBLIC_URL to this site&apos;s address, then restart
            the API.
          </AlertDescription>
        </Alert>
      ) : (
        <dl className="flex flex-col gap-4">
          {urls.map((u) => (
            <div key={u.field} className="flex min-w-0 flex-col gap-2">
              <dt className="text-sm font-medium">{u.label}</dt>
              <dd className="flex min-w-0 flex-col items-start gap-2">
                <span className="font-mono text-sm break-all">{u.value}</span>
                <CopyButton text={u.value} label={u.copy} />
              </dd>
            </div>
          ))}
        </dl>
      )}
    </ResponsiveDialog>
  );
}
