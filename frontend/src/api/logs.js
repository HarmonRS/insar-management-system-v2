import apiClient from './client';

export const listLogs = (logType) =>
  apiClient.get('/logs/list', { params: { log_type: logType } }).then(r => r.data);

export const getLogContent = (logPath, offset = 0, limit = 1000) =>
  apiClient.get(`/logs/content/${logPath}`, { params: { offset, limit } }).then(r => r.data);

export const deleteLog = (logPath) =>
  apiClient.delete(`/logs/${logPath}`).then(r => r.data);
