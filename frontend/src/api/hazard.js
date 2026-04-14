import apiClient from './client';

export const getHazardPoints = () => apiClient.get('/hazard-points').then(r => r.data);
export const scanHazardPoints = () => apiClient.post('/hazard-points/scan').then(r => r.data);
