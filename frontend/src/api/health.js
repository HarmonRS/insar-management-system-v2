import apiClient from './client';

export const getHealth = (params = {}) => apiClient.get('/health', { params }).then(r => r.data);
