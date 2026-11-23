"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { useRoles } from "@/components/access/use-catalogue";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { formFailureText } from "@/lib/access/bindings";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import { KEY_PATTERN } from "@/lib/flags/flag-form";

type S = components["schemas"];
type Provider = S["SsoProviderOut"];
type Protocol = S["SsoProtocol"];
type MetadataSource = "url" | "xml";

/** The protocols' names, as identity providers call them. */
export const PROTOCOL_LABELS: Record<Protocol, string> = {
  oidc: "OIDC",
  saml: "SAML",
};

const PROTOCOLS: Protocol[] = ["oidc", "saml"];
const DEFAULT_SCOPES = "openid, email, profile";
const SAVED_SECRET = "Saved — leave empty to keep it";
const KEY_HINT =
  "Use lowercase letters, digits, '.', '_' or '-', starting with a letter or digit";

type Draft = {
  protocol: Protocol;
  key: string;
  name: string;
  enabled: boolean;
  issuer: string;
  clientId: string;
  scopes: string;
  groupsClaim: string;
  clientSecret: string;
  metadataSource: MetadataSource;
  metadataUrl: string;
  metadataXml: string;
  spEntityId: string;
  emailAttribute: string;
  nameAttribute: string;
  groupsAttribute: string;
  spPrivateKey: string;
  spCertificate: string;
  jit: boolean;
  linkByEmail: boolean;
  /** A role key; "" for no role. */
  defaultRole: string;
};

type Field = keyof Draft;
type Errors = Partial<Record<Field, string>>;

function draftFrom(p: Provider | null): Draft {
  const saml = p?.saml ?? null;
  const metadataUrl = saml?.idp_metadata_url ?? "";
  return {
    protocol: p?.protocol ?? "oidc",
    key: p?.key ?? "",
    name: p?.name ?? "",
    enabled: p?.enabled ?? true,
    issuer: p?.oidc?.issuer ?? "",
    clientId: p?.oidc?.client_id ?? "",
    scopes: p?.oidc ? p.oidc.scopes.join(", ") : DEFAULT_SCOPES,
    groupsClaim: p?.oidc?.groups_claim ?? "groups",
    clientSecret: "",
    // With a URL, the stored XML is only the copy fetched on the last save.
    metadataSource: saml && !metadataUrl ? "xml" : "url",
    metadataUrl,
    metadataXml: metadataUrl ? "" : (saml?.idp_metadata_xml ?? ""),
    spEntityId: saml?.sp_entity_id ?? "",
    emailAttribute: saml?.email_attribute ?? "email",
    nameAttribute: saml?.name_attribute ?? "name",
    groupsAttribute: saml?.groups_attribute ?? "groups",
    spPrivateKey: "",
    spCertificate: "",
    jit: p?.jit_enabled ?? true,
    linkByEmail: p?.link_by_verified_email ?? false,
    defaultRole: p?.default_role ?? "",
  };
}

function scopeList(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function oidcBlock(d: Draft): S["OidcConfig"] {
  return {
    issuer: d.issuer.trim(),
    client_id: d.clientId.trim(),
    scopes: scopeList(d.scopes),
    groups_claim: d.groupsClaim.trim(),
  };
}

function samlBlock(d: Draft): S["SamlConfig"] {
  return {
    ...(d.metadataSource === "url"
      ? { idp_metadata_url: d.metadataUrl.trim() }
      : { idp_metadata_xml: d.metadataXml }),
    sp_entity_id: d.spEntityId.trim() || null,
    email_attribute: d.emailAttribute.trim(),
    name_attribute: d.nameAttribute.trim(),
    groups_attribute: d.groupsAttribute.trim(),
  };
}

function validate(d: Draft, editing: Provider | null): Errors {
  const e: Errors = {};
  if (d.name.trim() === "") e.name = "Enter a name";
  if (!editing && !KEY_PATTERN.test(d.key)) e.key = KEY_HINT;
  if (d.protocol === "oidc") {
    if (d.issuer.trim() === "") e.issuer = "Enter the issuer URL";
    if (d.clientId.trim() === "") e.clientId = "Enter the client ID";
    if (!scopeList(d.scopes).includes("openid"))
      e.scopes = "Include the openid scope";
    if (d.groupsClaim.trim() === "") e.groupsClaim = "Enter a claim name";
    if (d.clientSecret === "" && !editing?.has_client_secret)
      e.clientSecret = "Enter the client secret";
  } else {
    if (d.metadataSource === "url" && d.metadataUrl.trim() === "")
      e.metadataUrl = "Enter the metadata URL";
    if (d.metadataSource === "xml" && d.metadataXml.trim() === "")
      e.metadataXml = "Paste the metadata XML";
    for (const [field, value] of [
      ["emailAttribute", d.emailAttribute],
      ["nameAttribute", d.nameAttribute],
      ["groupsAttribute", d.groupsAttribute],
    ] as const)
      if (value.trim() === "") e[field] = "Enter an attribute name";
    if (d.spPrivateKey !== "" && d.spCertificate === "")
      e.spCertificate = "Paste the certificate that goes with the key";
    if (d.spCertificate !== "" && d.spPrivateKey === "")
      e.spPrivateKey = "Paste the key that goes with the certificate";
  }
  return e;
}

function toCreate(d: Draft): S["SsoProviderCreate"] {
  const common = {
    key: d.key,
    name: d.name.trim(),
    protocol: d.protocol,
    enabled: d.enabled,
    jit_enabled: d.jit,
    link_by_verified_email: d.linkByEmail,
    default_role: d.defaultRole || null,
  };
  if (d.protocol === "oidc")
    return { ...common, oidc: oidcBlock(d), client_secret: d.clientSecret };
  return {
    ...common,
    saml: samlBlock(d),
    ...(d.spPrivateKey && {
      sp_private_key: d.spPrivateKey,
      sp_certificate: d.spCertificate,
    }),
  };
}

/** Only what changed, so a SAML metadata URL isn't fetched again and secrets are kept. */
function toUpdate(d: Draft, p: Provider): S["SsoProviderUpdate"] {
  const was = draftFrom(p);
  const body: S["SsoProviderUpdate"] = {};
  if (d.name.trim() !== was.name) body.name = d.name.trim();
  if (d.enabled !== was.enabled) body.enabled = d.enabled;
  if (d.jit !== was.jit) body.jit_enabled = d.jit;
  if (d.linkByEmail !== was.linkByEmail)
    body.link_by_verified_email = d.linkByEmail;
  if (d.defaultRole !== was.defaultRole)
    body.default_role = d.defaultRole || null;
  if (d.protocol === "oidc") {
    if (JSON.stringify(oidcBlock(d)) !== JSON.stringify(oidcBlock(was)))
      body.oidc = oidcBlock(d);
    if (d.clientSecret) body.client_secret = d.clientSecret;
  } else {
    if (JSON.stringify(samlBlock(d)) !== JSON.stringify(samlBlock(was)))
      body.saml = samlBlock(d);
    if (d.spPrivateKey) {
      body.sp_private_key = d.spPrivateKey;
      body.sp_certificate = d.spCertificate;
    }
  }
  return body;
}

const PATH_FIELDS: Record<string, Field> = {
  key: "key",
  name: "name",
  default_role: "defaultRole",
  client_secret: "clientSecret",
  sp_private_key: "spPrivateKey",
  sp_certificate: "spCertificate",
  "oidc.issuer": "issuer",
  "oidc.client_id": "clientId",
  "oidc.scopes": "scopes",
  "oidc.groups_claim": "groupsClaim",
  "saml.idp_metadata_url": "metadataUrl",
  "saml.idp_metadata_xml": "metadataXml",
  "saml.sp_entity_id": "spEntityId",
  "saml.email_attribute": "emailAttribute",
  "saml.name_attribute": "nameAttribute",
  "saml.groups_attribute": "groupsAttribute",
};

/** The field a failed save belongs to; null when it belongs to the whole form. */
function failureField(f: ApiFailure): Field | null {
  if (f.status === 409 && f.code === "key_conflict") return "key";
  if (f.status !== 422 || !f.path) return null;
  return PATH_FIELDS[f.path.split("[")[0]] ?? null;
}

/** A labelled switch with a 44 px hit area. */
function LabelledSwitch({
  checked,
  onCheckedChange,
  label,
  help,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
  help?: string;
}) {
  return (
    <div className="flex flex-col">
      <label
        data-hit-area
        className="inline-flex min-h-11 min-w-11 items-center gap-2"
      >
        <Switch
          checked={checked}
          onCheckedChange={(value) => onCheckedChange(value)}
          aria-label={label}
        />
        <span className="text-sm font-medium">{label}</span>
      </label>
      {help && <p className="text-sm text-muted-foreground">{help}</p>}
    </div>
  );
}

function Form({
  provider,
  onSaved,
}: {
  provider: Provider | null;
  onSaved: () => void;
}) {
  const id = useId();
  const roles = useRoles();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(provider));
  // Field errors show only after the first submit.
  const [checked, setChecked] = useState(false);
  const [server, setServer] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const local = validate(draft, provider);
  const errors: Errors = checked ? { ...server, ...local } : server;

  const update = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setServer((e) => {
      const next = { ...e };
      for (const field of Object.keys(patch)) delete next[field as Field];
      return next;
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecked(true);
    if (Object.keys(local).length > 0) return;
    setBusy(true);
    setServer({});
    setFailure(null);
    try {
      if (provider) {
        await must(
          api.PATCH("/api/sso/providers/{key}", {
            params: { path: { key: provider.key } },
            body: toUpdate(draft, provider),
          }),
        );
        toast("Provider saved");
      } else {
        await must(api.POST("/api/sso/providers", { body: toCreate(draft) }));
        toast("Provider added");
      }
      onSaved();
    } catch (err) {
      setBusy(false);
      if (!(err instanceof ApiError)) {
        console.error("saving the SSO provider failed", err);
        setFailure("Couldn't reach the server");
        return;
      }
      const field = failureField(err.failure);
      if (field === null) setFailure(formFailureText(err.failure));
      else if (err.failure.code === "key_conflict")
        setServer({ key: "Another provider already uses this key" });
      else setServer({ [field]: err.failure.message });
    }
  };

  const errorId = (field: Field) => `${id}-${field}-error`;
  const describedBy = (field: Field, help?: boolean) =>
    [help && `${id}-${field}-help`, errors[field] && errorId(field)]
      .filter(Boolean)
      .join(" ") || undefined;

  /** A text input or textarea with its label, help and error. */
  const text = (
    field: Field,
    label: string,
    {
      help,
      multiline = false,
      secret = false,
      mono = false,
      placeholder,
      type,
    }: {
      help?: string;
      multiline?: boolean;
      secret?: boolean;
      mono?: boolean;
      placeholder?: string;
      type?: string;
    } = {},
  ) => {
    const props = {
      id: `${id}-${field}`,
      value: draft[field] as string,
      placeholder,
      autoComplete: "off",
      autoCapitalize: "none",
      spellCheck: false,
      className: mono ? "font-mono" : undefined,
      "aria-invalid": errors[field] ? true : undefined,
      "aria-describedby": describedBy(field, Boolean(help)),
      onChange: (
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
      ) => update({ [field]: e.target.value }),
    };
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={props.id}>{label}</Label>
        {multiline ? (
          <Textarea {...props} />
        ) : (
          <Input
            {...props}
            type={secret ? "password" : type}
            autoComplete={secret ? "new-password" : "off"}
          />
        )}
        {help && (
          <p
            id={`${id}-${field}-help`}
            className="text-sm text-muted-foreground"
          >
            {help}
          </p>
        )}
        {errors[field] && (
          <p id={errorId(field)} className="text-sm text-destructive">
            {errors[field]}
          </p>
        )}
      </div>
    );
  };

  const radios = <T extends string>(
    legend: string,
    name: string,
    options: { value: T; label: string }[],
    value: T,
    onChange: (value: T) => void,
  ) => (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      <div
        role="radiogroup"
        aria-label={legend}
        className="flex flex-wrap gap-x-6"
      >
        {options.map((o) => (
          <label
            key={o.value}
            data-hit-area
            className="inline-flex min-h-11 min-w-11 items-center gap-2"
          >
            <input
              type="radio"
              name={`${id}-${name}`}
              className="size-5 shrink-0 accent-primary"
              checked={value === o.value}
              onChange={() => onChange(o.value)}
            />
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  );

  const keptSecret = provider?.has_client_secret ? SAVED_SECRET : undefined;
  const keptKey = provider?.has_sp_key ? SAVED_SECRET : undefined;

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      {!provider &&
        radios(
          "Protocol",
          "protocol",
          PROTOCOLS.map((p) => ({ value: p, label: PROTOCOL_LABELS[p] })),
          draft.protocol,
          (protocol) => update({ protocol }),
        )}
      {text("name", "Name", {
        placeholder: "Okta",
        help: "Shown on the sign-in page",
      })}
      {!provider &&
        text("key", "Key", {
          mono: true,
          help: "Part of the sign-in and callback URLs; it can't change later",
        })}
      <LabelledSwitch
        label="Enabled"
        checked={draft.enabled}
        onCheckedChange={(enabled) => update({ enabled })}
      />

      <Separator />
      {draft.protocol === "oidc" ? (
        <>
          {text("issuer", "Issuer", {
            type: "url",
            placeholder: "https://login.example.com",
          })}
          {text("clientId", "Client ID")}
          {text("clientSecret", "Client secret", {
            secret: true,
            placeholder: keptSecret,
          })}
          {text("scopes", "Scopes", { help: "Separated by commas" })}
          {text("groupsClaim", "Groups claim", {
            help: "The claim that lists the user's groups",
          })}
        </>
      ) : (
        <>
          {radios(
            "IdP metadata",
            "metadata",
            [
              { value: "url", label: "From a URL" },
              { value: "xml", label: "Paste the XML" },
            ],
            draft.metadataSource,
            (metadataSource) => update({ metadataSource }),
          )}
          {draft.metadataSource === "url"
            ? text("metadataUrl", "Metadata URL", {
                type: "url",
                help: "MegooFlags fetches it each time you save",
              })
            : text("metadataXml", "Metadata XML", {
                multiline: true,
                mono: true,
              })}
          {text("spEntityId", "SP entity ID", {
            help: "Leave empty to use this provider's metadata URL",
          })}
          {text("emailAttribute", "Email attribute")}
          {text("nameAttribute", "Name attribute")}
          {text("groupsAttribute", "Groups attribute")}
          {text("spPrivateKey", "SP private key", {
            multiline: !keptKey,
            secret: Boolean(keptKey),
            mono: true,
            placeholder: keptKey,
            help: "Optional: with the certificate, MegooFlags signs its requests",
          })}
          {text("spCertificate", "SP certificate", {
            multiline: !keptKey,
            secret: Boolean(keptKey),
            mono: true,
            placeholder: keptKey,
          })}
        </>
      )}

      <Separator />
      <LabelledSwitch
        label="Create users on first sign-in"
        help="Without it, only people who already have an account can sign in."
        checked={draft.jit}
        onCheckedChange={(jit) => update({ jit })}
      />
      <LabelledSwitch
        label="Link by verified email"
        help="Signs an existing user in when the identity provider asserts their verified email."
        checked={draft.linkByEmail}
        onCheckedChange={(linkByEmail) => update({ linkByEmail })}
      />
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor={`${id}-defaultRole`}>Default role</Label>
        <NativeSelect
          id={`${id}-defaultRole`}
          value={draft.defaultRole}
          onChange={(e) => update({ defaultRole: e.target.value })}
          aria-invalid={errors.defaultRole ? true : undefined}
          aria-describedby={describedBy("defaultRole", true)}
        >
          <option value="">No role</option>
          {(roles ?? []).map((r) => (
            <option key={r.key} value={r.key}>
              {r.name}
            </option>
          ))}
          {/* Until the roles load, the current one still shows. */}
          {roles === null && draft.defaultRole && (
            <option value={draft.defaultRole}>{draft.defaultRole}</option>
          )}
        </NativeSelect>
        <p
          id={`${id}-defaultRole-help`}
          className="text-sm text-muted-foreground"
        >
          Granted for the whole organization to everyone who signs in this way
        </p>
        {errors.defaultRole && (
          <p id={errorId("defaultRole")} className="text-sm text-destructive">
            {errors.defaultRole}
          </p>
        )}
      </div>

      {failure && (
        <p role="alert" className="text-sm break-words text-destructive">
          {failure}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          {provider ? "Save" : "Add provider"}
        </Button>
      </div>
    </form>
  );
}

/** Adds an OIDC or SAML provider (`provider` null), or edits one. */
export function ProviderForm({
  provider,
  open,
  onOpenChange,
  onSaved,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  provider: Provider | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={provider ? `Edit ${provider.name}` : "Add SSO provider"}
      description={
        provider
          ? `${PROTOCOL_LABELS[provider.protocol]} · ${provider.key}`
          : "People sign in through your identity provider with OIDC or SAML."
      }
    >
      {open && (
        <Form
          key={provider?.id ?? "new"}
          provider={provider}
          onSaved={onSaved}
        />
      )}
    </ResponsiveDialog>
  );
}
