import apiClient from './client';

export const getAssetInventoryStatus = () =>
  apiClient.get('/assets/inventory/status').then(r => r.data);

export const scanAssetInventory = (payload = {}) =>
  apiClient.post('/assets/inventory/scan', payload).then(r => r.data);

export const listSourceAssets = (params = {}) =>
  apiClient.get('/assets/sources', { params }).then(r => r.data);

export const listOrbitAssets = (params = {}) =>
  apiClient.get('/assets/orbits', { params }).then(r => r.data);

export const listAssetIssues = (params = {}) =>
  apiClient.get('/assets/issues', { params }).then(r => r.data);

export const unpackSentinel1Source = (assetId, payload = {}) =>
  apiClient.post(`/assets/sources/${assetId}/unpack-sentinel1`, payload).then(r => r.data);

export const unpackSentinel1Batch = (payload = {}) =>
  apiClient.post('/assets/inventory/unpack-sentinel1', payload).then(r => r.data);
