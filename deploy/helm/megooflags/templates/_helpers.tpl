{{/*
The chart name.
*/}}
{{- define "megooflags.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The release's base name: the release name when it already contains "megooflags", else
"<release>-megooflags", cut to 41 characters and without trailing "-" or ".". 41 leaves room for
the longest suffix on a StatefulSet ("-postgresql", 11), whose name may have 52 characters because
Kubernetes adds "-<hash>" (up to 11) in the 63-character controller-revision-hash label. It also
leaves room for "-migrate-<revision>" and the worker pods' "-worker-<ordinal>".
*/}}
{{- define "megooflags.fullname" -}}
{{- $name := include "megooflags.name" . -}}
{{- $base := ternary .Release.Name (printf "%s-%s" .Release.Name $name) (contains $name .Release.Name) -}}
{{- regexReplaceAll "[-.]+$" ($base | trunc 41) "" -}}
{{- end -}}

{{/*
Metadata labels. Not for selectors: they change with the chart version.
*/}}
{{- define "megooflags.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "megooflags.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels of one component. Takes (dict "ctx" $ "component" "api").
*/}}
{{- define "megooflags.selectorLabels" -}}
app.kubernetes.io/name: {{ include "megooflags.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
An image reference. Takes (dict "ctx" $ "name" "megooflags-api"). The tag defaults to
the chart's appVersion; the registry prefix is left out when image.registry is empty.
*/}}
{{- define "megooflags.image" -}}
{{- $image := .ctx.Values.image -}}
{{- $tag := default .ctx.Chart.AppVersion $image.tag -}}
{{- if $image.registry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $image.registry) .name $tag -}}
{{- else -}}
{{- printf "%s:%s" .name $tag -}}
{{- end -}}
{{- end -}}

{{/*
The Secret the pods read: existingSecret, or the chart's own.
*/}}
{{- define "megooflags.secretName" -}}
{{- default (include "megooflags.fullname" .) .Values.existingSecret -}}
{{- end -}}

{{/*
The ConfigMap the pods read.
*/}}
{{- define "megooflags.configName" -}}
{{- printf "%s-config" (include "megooflags.fullname" .) -}}
{{- end -}}

{{/*
Pod settings shared by the api, web and migration pods: no service account token, and
the images' uid 10001.
*/}}
{{- define "megooflags.podSecurity" -}}
{{- include "megooflags.podSecurityAs" (dict "ctx" . "uid" 10001) -}}
{{- end -}}

{{/*
Pod settings with another uid, as podSecurity. Takes (dict "ctx" $ "uid" 999): 999 is the
postgres and valkey images' own user.
*/}}
{{- define "megooflags.podSecurityAs" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid }}
  runAsGroup: {{ .uid }}
  fsGroup: {{ .uid }}
{{- with .ctx.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Container securityContext of every workload.
*/}}
{{- define "megooflags.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
{{- end -}}

{{/*
Pod annotations: checksums of the ConfigMap and the chart's Secret (none with
existingSecret), so a change restarts the pods. Takes (dict "ctx" $ "extra" <podAnnotations>).
*/}}
{{- define "megooflags.podAnnotations" -}}
checksum/config: {{ include (print .ctx.Template.BasePath "/configmap.yaml") .ctx | sha256sum }}
{{- if not .ctx.Values.existingSecret }}
checksum/secret: {{ include (print .ctx.Template.BasePath "/secret.yaml") .ctx | sha256sum }}
{{- end }}
{{- with .extra }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
envFrom for pods that read the settings and the secrets: the ConfigMap, then the Secret.
*/}}
{{- define "megooflags.envFrom" -}}
envFrom:
  - configMapRef:
      name: {{ include "megooflags.configName" . }}
  - secretRef:
      name: {{ include "megooflags.secretName" . }}
{{- end -}}

{{/*
nodeSelector, tolerations and affinity of a component's values, when set.
*/}}
{{- define "megooflags.scheduling" -}}
{{- with .nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
The paths routed to the API, with the types deploy/Caddyfile and deploy/Caddyfile.worker give them
("/x/*" is a prefix). Workers serve no /api. A YAML list of {type, path}. The HTTPRoute uses the
types as they are; the Ingress makes every path Prefix (see ingress.yaml).
*/}}
{{- define "megooflags.apiPaths" -}}
{{- if ne .Values.role "worker" }}
- {type: Prefix, path: /api}
{{- end }}
- {type: Prefix, path: /sdk}
- {type: Prefix, path: /ofrep}
- {type: Exact, path: /healthz}
- {type: Exact, path: /readyz}
{{- end -}}

{{/*
The Service that serves the API paths: the api, or the worker on worker releases.
*/}}
{{- define "megooflags.apiService" -}}
{{- include "megooflags.fullname" . }}-{{ eq .Values.role "worker" | ternary "worker" "api" }}
{{- end -}}
