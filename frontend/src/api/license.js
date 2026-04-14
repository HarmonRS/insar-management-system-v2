import apiClient from './client';

export const getLicenseStatus = () => apiClient.get('/license/status').then(r => r.data);
export const uploadLicense = (formData) => apiClient.post('/license/upload', formData).then(r => r.data);
export const refreshLicense = () => apiClient.post('/license/refresh').then(r => r.data);
