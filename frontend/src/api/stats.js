import apiClient from './client';

export const getStatistics = (fresh = false) =>
    apiClient.get('/statistics', { params: fresh ? { fresh: true } : undefined }).then(r => r.data);
