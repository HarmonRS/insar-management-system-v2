import apiClient from './client';

export const getUnpackConfig = () => apiClient.get('/unpack/config').then(r => r.data);
export const runUnpack = (data) => apiClient.post('/unpack/run', data).then(r => r.data);
