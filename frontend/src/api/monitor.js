import apiClient from './client';

export const getMonitorStatus = () => apiClient.get('/monitor/status').then(r => r.data);
export const getMonitorLogs = () => apiClient.get('/monitor/logs').then(r => r.data);
export const runMonitorNow = () => apiClient.post('/monitor/run-now').then(r => r.data);
export const saveMonitorConfig = (config) => apiClient.post('/monitor/config', config).then(r => r.data);
