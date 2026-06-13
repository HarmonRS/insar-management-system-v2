import apiClient from './client';

export const getEnviStatus = () => apiClient.get('/idl/status').then(r => r.data);
export const launchIdlWorkbench = () => apiClient.post('/idl/launch-workbench').then(r => r.data);
export const inspectImport = (rootDir) =>
  apiClient.post('/idl/inspect/import', { root_dir: rootDir }).then(r => r.data);
export const inspectDinsar = (rootDir) =>
  apiClient.post('/idl/inspect/dinsar', { root_dir: rootDir }).then(r => r.data);
export const queueImportJob = (payload) =>
  apiClient.post('/idl/jobs/import', payload).then(r => r.data);
export const queueDinsarJob = (payload) =>
  apiClient.post('/idl/jobs/dinsar', payload).then(r => r.data);
export const getRecentRuns = (limit = 20) =>
  apiClient.get(`/idl/jobs/recent?limit=${encodeURIComponent(limit)}`).then(r => r.data);
export const getTaskLogs = (taskId, limit = 50, offset = 0) =>
  apiClient.get(`/tasks/${taskId}/logs?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`).then(r => r.data);
export const forceCancelTask = (taskId, password) =>
  apiClient.post(`/tasks/${taskId}/force-cancel`, { password }).then(r => r.data);
export const extractDispResults = (rootDir, destDir = null) =>
  apiClient.post('/idl/extract-disp', { root_dir: rootDir, dest_dir: destDir }).then(r => r.data);
export const getTaskOverview = (rootDir) =>
  apiClient.get('/idl/task-overview', { params: { root_dir: rootDir } }).then(r => r.data);
export const getJobLog = (runId) =>
  apiClient.get(`/idl/jobs/${encodeURIComponent(runId)}/log`).then(r => r.data);
export const deleteRun = (runId) =>
  apiClient.delete(`/idl/jobs/${encodeURIComponent(runId)}`).then(r => r.data);
