import apiClient from './client';

export const getImagingDates = () => apiClient.get('/radar-data/imaging-dates').then(r => r.data);
export const getSearchOptions = () => apiClient.get('/radar-data/search/options').then(r => r.data);
export const searchRadarData = (params) => apiClient.post('/radar-data/search', params).then(r => r.data);
export const getPreviewStatus = (itemId) => apiClient.get(`/radar-data/${itemId}/preview-status`).then(r => r.data);
export const rebuildPreviewCache = (itemId) => apiClient.post(`/radar-data/${itemId}/rebuild-preview-cache`).then(r => r.data);
export const scanData = (params) => apiClient.post('/scan-data', params).then(r => r.data);
export const getAvailableSatellites = () => apiClient.get('/radar-data/available-satellites').then(r => r.data);
