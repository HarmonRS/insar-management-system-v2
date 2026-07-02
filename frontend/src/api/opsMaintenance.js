import apiClient from './client';

export const listMaintenanceTasks = (params = {}) =>
  apiClient.get('/ops-maintenance/tasks', { params }).then(r => r.data);

export const getMaintenanceTaskDiagnosis = (taskId) =>
  apiClient.get(`/ops-maintenance/tasks/${encodeURIComponent(taskId)}/diagnosis`).then(r => r.data);

export const previewMaintenanceCleanup = (taskId) =>
  apiClient.post(`/ops-maintenance/tasks/${encodeURIComponent(taskId)}/cleanup-preview`).then(r => r.data);

export const cleanupMaintenanceTask = (taskId, payload) =>
  apiClient.post(`/ops-maintenance/tasks/${encodeURIComponent(taskId)}/cleanup`, payload).then(r => r.data);
