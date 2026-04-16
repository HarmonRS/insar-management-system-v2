import apiClient from './client';

export const getActiveTasks = () => apiClient.get('/tasks/active').then(r => r.data);
export const getTask = (taskId) => apiClient.get(`/tasks/${taskId}`).then(r => r.data);
export const getTaskLogs = (taskId, limit = 50, offset = 0) =>
  apiClient.get(`/tasks/${taskId}/logs?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`).then(r => r.data);
export const deleteTaskLog = (taskId, logId) =>
  apiClient.delete(`/tasks/${taskId}/logs/${encodeURIComponent(logId)}`).then(r => r.data);
export const clearTaskLogs = (taskId) =>
  apiClient.delete(`/tasks/${taskId}/logs`).then(r => r.data);
