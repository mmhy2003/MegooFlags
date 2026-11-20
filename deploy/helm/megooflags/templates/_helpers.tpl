{{/*
The chart name.
*/}}
{{- define "megooflags.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The release's base name: the release name when it already contains "megooflags",
else "<release>-megooflags", cut to 63 characters.
*/}}
{{- define "megooflags.fullname" -}}
{{- $name := include "megooflags.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
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
