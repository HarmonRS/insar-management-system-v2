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

export const runSbasInsarBaselineAudit = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/baseline-audit`, payload).then(r => r.data);

export const decideSbasInsarItab = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/itab-decision`, payload).then(r => r.data);

export const prepareSbasInsarCoregistration = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/coregistration`, payload).then(r => r.data);

export const submitSbasInsarCoregistrationJob = (runId, payload = {}) =>
  apiClient.post(`/sbas-insar-production/runs/${encodeURIComponent(runId)}/coregistration/jobs`, payload).then(r => r.data);

export const getSbasInsarRunArtifactUrl = (runId, relativePath) =>
  `/api/sbas-insar-production/runs/${encodeURIComponent(runId)}/artifacts/${encodeArtifactPath(relativePath)}`;

export const listSbasInsarTrialRuns = () =>
  apiClient.get('/sbas-insar-production/trial-runs').then(r => r.data);

export const getSbasInsarTrialRun = trialId =>
  apiClient.get(`/sbas-insar-production/trial-runs/${encodeURIComponent(trialId)}`).then(r => r.data);

export const getSbasInsarArtifactUrl = (trialId, relativePath) =>
  `/api/sbas-insar-production/trial-runs/${encodeURIComponent(trialId)}/artifacts/${encodeArtifactPath(relativePath)}`;
