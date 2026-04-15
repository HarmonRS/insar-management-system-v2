import apiClient from './client';

export const findPairs = (formData) => apiClient.post('/find-pairs', formData).then(r => r.data);
export const findPsTimeseries = (formData) => apiClient.post('/find-ps-timeseries', formData).then(r => r.data);
export const getPairingHealth = () => apiClient.get('/pairing/health').then(r => r.data);
export const rebuildPairingCache = () => apiClient.post('/pairing/rebuild-cache').then(r => r.data);
export const reconcileDirtyPairingCache = (forceFull = false) => (
  apiClient.post('/pairing/reconcile-dirty', null, { params: { force_full: forceFull } }).then(r => r.data)
);
