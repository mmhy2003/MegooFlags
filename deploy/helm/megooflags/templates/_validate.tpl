{{/*
Fails the render when a required value is missing or wrong. Included at the top of
configmap.yaml, which every role renders.
*/}}
{{- define "megooflags.validate" -}}
{{- $v := .Values -}}
{{- if not (has $v.role (list "standalone" "control-plane" "worker")) -}}
{{- fail "role must be standalone, control-plane or worker" -}}
{{- end -}}
{{- if eq $v.role "worker" -}}
{{- if not $v.worker.controlPlaneUrl -}}
{{- fail "worker.controlPlaneUrl is required when role is worker" -}}
{{- end -}}
{{- if not (or $v.worker.nodeTokens (and $v.existingSecret (gt (int $v.worker.replicas) 0))) -}}
{{- fail "worker.nodeTokens is required when role is worker (one token per replica, from the Nodes page), or set existingSecret and worker.replicas" -}}
{{- end -}}
{{- else -}}
{{- if not $v.existingSecret -}}
{{- if not $v.secretKey -}}
{{- fail "secretKey is required (32+ characters, e.g. openssl rand -hex 32), or set existingSecret" -}}
{{- end -}}
{{- if and (not $v.postgresql.enabled) (not $v.externalDatabase.url) -}}
{{- fail "externalDatabase.url is required when postgresql.enabled is false" -}}
{{- end -}}
{{- if and (not $v.valkey.enabled) (not $v.externalValkey.url) -}}
{{- fail "externalValkey.url is required when valkey.enabled is false" -}}
{{- end -}}
{{- end -}}
{{- if and $v.secretKey (lt (len $v.secretKey) 32) -}}
{{- fail "secretKey must be at least 32 characters" -}}
{{- end -}}
{{- if and $v.externalDatabase.url (not (hasPrefix "postgresql+asyncpg://" $v.externalDatabase.url)) -}}
{{- fail "externalDatabase.url must use the postgresql+asyncpg:// driver" -}}
{{- end -}}
{{- end -}}
{{- end -}}
