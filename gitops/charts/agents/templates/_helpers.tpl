{{- define "agents.labels" -}}
app.kubernetes.io/part-of: kiroween-agents
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* Image reference; refuses "latest" (REQ-CHT-07). Call with (dict "root" $ "image" ...). */}}
{{- define "agents.image" -}}
{{- if eq (toString .image.tag) "latest" -}}
{{- fail "image tag must be a commit SHA, not latest" -}}
{{- end -}}
{{ .root.Values.image.registry }}/{{ .image.repository }}:{{ .image.tag }}
{{- end -}}

{{- define "agents.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "agents.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "agents.probes" -}}
livenessProbe:
  httpGet: {path: /healthz, port: api}
  initialDelaySeconds: 10
  periodSeconds: 20
  timeoutSeconds: 3
readinessProbe:
  httpGet: {path: /readyz, port: api}
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 3
{{- end -}}

{{- define "agents.commonEnv" -}}
- name: AWS_REGION
  value: {{ .Values.region | quote }}
- name: HOME
  value: /tmp
- name: LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: API_PORT
  value: {{ .Values.ports.api | quote }}
- name: METRICS_PORT
  value: {{ .Values.ports.metrics | quote }}
- name: MAX_MODEL_CALLS
  value: {{ .Values.maxModelCalls | quote }}
- name: MAX_TOKENS
  value: {{ .Values.maxTokens | quote }}
- name: PRICES_JSON
  valueFrom:
    configMapKeyRef:
      name: agents-config
      key: prices.json
{{- end -}}

{{- define "agents.tmpVolume" -}}
- name: tmp
  emptyDir:
    sizeLimit: 64Mi
{{- end -}}
