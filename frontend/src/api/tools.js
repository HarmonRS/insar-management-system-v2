import apiClient from './client';

export const copyPsStack = (data) => apiClient.post('/tools/copy-ps-stack', data).then(r => r.data);
export const copyDinsarPairs = (data) => apiClient.post('/tools/copy-dinsar-pairs', data).then(r => r.data);
export const getCopyStatus = (taskId) => apiClient.get(`/tools/copy-status/${taskId}`).then(r => r.data);
