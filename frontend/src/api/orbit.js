import apiClient from './client';

export const getOrbitStatus = () =>
  apiClient.get('/orbit/status').then(r => r.data);

export const syncOrbitPools = (payload = {}) =>
  apiClient.post('/orbit/sync-pools', payload).then(r => r.data);

export const organizeOrbits = () =>
  apiClient.post('/orbit/organize').then(r => r.data);
