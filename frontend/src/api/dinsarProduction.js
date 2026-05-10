import apiClient from './client';

// 引擎状态
export const listEngines = () =>
  apiClient.get('/dinsar-production/engines').then(r => r.data);

export const getEngineDetail = (engineCode) =>
  apiClient.get(`/dinsar-production/engines/${encodeURIComponent(engineCode)}`).then(r => r.data);

// WSL 校验（管理员）
export const runWslCheck = (payload = {}) =>
  apiClient.post('/dinsar-production/wsl-check', payload).then(r => r.data);

// 提交生产任务
export const submitRun = (payload) =>
  apiClient.post('/dinsar-production/run', payload).then(r => r.data);

// 运行历史
export const listRuns = (limit = 20, offset = 0) =>
  apiClient.get(
    `/dinsar-production/runs?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
  ).then(r => r.data);

export const getRunLog = (runId) =>
  apiClient.get(`/dinsar-production/runs/${encodeURIComponent(runId)}/log`).then(r => r.data);

export const deleteRunLog = (runId) =>
  apiClient.delete(`/dinsar-production/runs/${encodeURIComponent(runId)}/log`).then(r => r.data);

export const deleteRunRecord = (runId) =>
  apiClient.delete(`/dinsar-production/runs/${encodeURIComponent(runId)}`).then(r => r.data);

export const previewPyintInputAssets = (payload) =>
  apiClient.post('/dinsar-production/engines/pyint/preview-input-assets', payload).then(r => r.data);
