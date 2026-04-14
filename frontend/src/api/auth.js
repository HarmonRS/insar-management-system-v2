import apiClient from './client';

export const getMe = () => apiClient.get('/auth/me').then(r => r.data);
export const login = (username, password) => apiClient.post('/auth/login', { username, password }).then(r => r.data);
export const logout = () => apiClient.post('/auth/logout').then(r => r.data);
export const getUsers = () => apiClient.get('/auth/users').then(r => r.data);
export const createUser = (data) => apiClient.post('/auth/users', data).then(r => r.data);
export const updateUser = (id, data) => apiClient.patch(`/auth/users/${id}`, data).then(r => r.data);
export const getAuditLogs = (params) => apiClient.get('/auth/audit-logs', { params }).then(r => r.data);
export const cleanupSessions = () => apiClient.post('/auth/cleanup-sessions').then(r => r.data);
