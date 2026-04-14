import apiClient from './client';

export const createTimeseriesRun = payload =>
  apiClient.post('/timeseries-production/runs', payload).then(r => r.data);

export const listTimeseriesRuns = (params = {}) =>
  apiClient.get('/timeseries-production/runs', { params }).then(r => r.data);

export const getTimeseriesRunDetail = runId =>
  apiClient.get(`/timeseries-production/runs/${encodeURIComponent(runId)}`).then(r => r.data);
