import apiClient from './client';

export const getActiveTasks = () => apiClient.get('/tasks/active').then(r => r.data);
export const getTask = (taskId) => apiClient.get(`/tasks/${taskId}`).then(r => r.data);
