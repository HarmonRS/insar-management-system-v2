import apiClient from './client';

const encodeArtifactPath = relativePath =>
  String(relativePath || '')
    .split('/')
    .map(segment => encodeURIComponent(segment))
    .join('/');

export const getSbasInsarCapabilities = () =>
  apiClient.get('/sbas-insar-production/capabilities').then(r => r.data);

export const discoverSbasInsarStacks = (payload = {}) =>
  apiClient.post('/sbas-insar-production/stacks/discover', payload).then(r => r.data);

export const auditSbasInsarStack = (stackId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/stacks/${encodeURIComponent(stackId)}/audit`, payload).then(r => r.data);

export const submitSbasInsarRun = (stackId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/stacks/${encodeURIComponent(stackId)}/runs`, payload).then(r => r.data);

export const listSbasInsarRuns = () =>
  apiClient.get('/sbas-insar-production/runs').then(r => r.data);

export const getSbasInsarRun = runId =>
  apiClient.get(`/sbas-insar-production/runs/${encodeURIComponent(runId)}`).then(r => r.data);

export const deleteSbasInsarRun = runId =>
  apiClient.delete(`/sbas-insar-production/runs/${encodeURIComponent(runId)}`).then(r => r.data);

export const prepareSbasInsarWorkflow = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/workflow`, payload).then(r => r.data);

export const submitSbasInsarWorkflowJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/workflow/jobs`, payload).then(r => r.data);

export const runSbasInsarBaselineAudit = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/baseline-audit`, payload).then(r => r.data);

export const decideSbasInsarItab = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/itab-decision`, payload).then(r => r.data);

export const prepareSbasInsarCoregistration = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/coregistration`, payload).then(r => r.data);

export const submitSbasInsarCoregistrationJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/coregistration/jobs`, payload).then(r => r.data);

export const prepareSbasInsarRdcDem = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/rdc-dem`, payload).then(r => r.data);

export const submitSbasInsarRdcDemJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/rdc-dem/jobs`, payload).then(r => r.data);

export const prepareSbasInsarInterferograms = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/interferograms`, payload).then(r => r.data);

export const submitSbasInsarInterferogramsJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/interferograms/jobs`, payload).then(r => r.data);

export const prepareSbasInsarIptaTimeseries = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/ipta-timeseries`, payload).then(r => r.data);

export const submitSbasInsarIptaTimeseriesJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/ipta-timeseries/jobs`, payload).then(r => r.data);

export const getSbasInsarRunArtifactUrl = (runId, relativePath) =>
  `/api/sbas-insar-production/runs/${encodeURIComponent(runId)}/artifacts/${encodeArtifactPath(relativePath)}`;

export const getLandsarSbasCapabilities = () =>
  apiClient.get('/sbas-insar-production/landsar/capabilities').then(r => r.data);

export const submitLandsarSbasAutoWorkflow = (payload = {}) =>
  apiClient.post('/sbas-insar-production/landsar/workflows/auto', payload).then(r => r.data);

export const listLandsarSbasRuns = () =>
  apiClient.get('/sbas-insar-production/landsar/runs').then(r => r.data);

export const getLandsarSbasRun = runId =>
  apiClient.get(`/sbas-insar-production/landsar/runs/${encodeURIComponent(runId)}`).then(r => r.data);

export const getLandsarSbasRunArtifactUrl = (runId, relativePath) =>
  `/api/sbas-insar-production/landsar/runs/${encodeURIComponent(runId)}/artifacts/${encodeArtifactPath(relativePath)}`;
