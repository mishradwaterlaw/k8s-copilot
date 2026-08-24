{{/*
_helpers.tpl — Reusable template snippets for the k8s-copilot chart.

CONCEPT: HELM TEMPLATES AND THE GO TEMPLATE LANGUAGE
═════════════════════════════════════════════════════
Helm templates use Go's text/template package with extra Helm/Sprig functions.

Syntax overview:
  {{ .Values.image.repository }}   → access a value from values.yaml
  {{ .Release.Name }}              → the Helm release name (set at install time)
  {{ .Chart.Name }}                → "k8s-copilot" (from Chart.yaml)
  {{- ... -}}                      → the dash trims whitespace before/after the block
  {{ include "template.name" . }}  → call a named template, passing current context (.)
  {{ if .Values.rbac.clusterWide }}  → conditional
  {{ toYaml .Values.resources | nindent 12 }}  → convert to YAML and indent 12 spaces

CONCEPT: _helpers.tpl
══════════════════════
Files starting with _ are NOT rendered as manifests (Helm skips them).
They only define named templates that OTHER templates can call with {{ include }}.

This avoids copy-pasting the same name-generation logic across every manifest.
If the naming convention changes, you change it once here, not in 10 files.
*/}}


{{/*
Expand the name of the chart.
"k8s-copilot" → used as a base for all resource names.
We truncate at 63 chars because Kubernetes DNS label limit is 63 chars.
*/}}
{{- define "k8s-copilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Create a fully-qualified app name.
Format: <release-name>-<chart-name>  e.g. "prod-k8s-copilot"
If the release name already contains the chart name, don't duplicate it.
e.g. release="k8s-copilot" + chart="k8s-copilot" → "k8s-copilot" (not "k8s-copilot-k8s-copilot")

This is the name used for the Deployment, Service, ServiceAccount, etc.
*/}}
{{- define "k8s-copilot.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}


{{/*
Create chart label value: "<chart-name>-<chart-version>"  e.g. "k8s-copilot-1.0.0"
Used in the "helm.sh/chart" label on all resources so you can tell which
chart version created each resource.
*/}}
{{- define "k8s-copilot.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Common labels applied to EVERY resource in this chart.
Labels enable:
  - kubectl get all -l app.kubernetes.io/name=k8s-copilot
  - Helm knows which resources belong to this release (for helm uninstall)
  - Monitoring tools can discover resources by label

app.kubernetes.io/* labels follow the official Kubernetes recommended label set.
*/}}
{{- define "k8s-copilot.labels" -}}
helm.sh/chart: {{ include "k8s-copilot.chart" . }}
{{ include "k8s-copilot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}


{{/*
Selector labels — used by the Deployment to match its Pods, and by the Service
to find Pods to route traffic to.

CRITICAL: These must be STABLE (never change after first deploy).
If selector labels change, Kubernetes rejects the Deployment update because
it can't reconcile old Pods (with old labels) with the new selector.
This is why selectorLabels is separate from labels — the extra labels
(chart version, managed-by) CAN change; selectorLabels CANNOT.
*/}}
{{- define "k8s-copilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "k8s-copilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}


{{/*
ServiceAccount name to use.
If serviceAccount.create=true and no name is specified, use the fullname.
If serviceAccount.create=false, use the overridden name (so you can point
to a pre-existing ServiceAccount you manage yourself).
*/}}
{{- define "k8s-copilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "k8s-copilot.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
