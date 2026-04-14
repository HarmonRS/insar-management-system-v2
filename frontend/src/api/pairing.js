import apiClient from './client';

export const findPairs = (formData) => apiClient.post('/find-pairs', formData).then(r => r.data);
export const findPsTimeseries = (formData) => apiClient.post('/find-ps-timeseries', formData).then(r => r.data);
