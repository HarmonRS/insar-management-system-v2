import apiClient from './client';

export const createTimeseriesRun = payload =>
  apiClient.post('/timeseries-production/runs', payload).then(r => r.data);

export const runTimeseriesWslCheck = (payload = {}) =>
  apiClient.post('/timeseries-production/wsl-check', payload).then(r => r.data);

export const runTimeseriesPreflight = payload =>
  apiClient.post('/timeseries-production/preflight', payload).then(r => r.data);

export const listTimeseriesRuns = (params = {}) =>
  apiClient.get('/timeseries-production/runs', { params }).then(r => r.data);

export const getTimeseriesRunDetail = runId =>
  apiClient.get(`/timeseries-production/runs/${encodeURIComponent(runId)}`).then(r => r.data);

export const retryTimeseriesStep = (runId, payload) =>
  apiClient.post(`/timeseries-production/runs/${encodeURIComponent(runId)}/retry-step`, payload).then(r => r.data);
