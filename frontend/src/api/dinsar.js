import apiClient from './client';

export const getDinsarResults = (params) => apiClient.get('/dinsar-results', { params }).then(r => r.data);
export const labelDinsarResult = (resultId, label) =>
    apiClient.post(`/dinsar-results/${resultId}/label`, { label }).then(r => r.data);
export const scanDinsarResults = (params) => apiClient.post('/scan-dinsar-results', params).then(r => r.data);
export const exportDinsarResults = (resultIds, targetDir) =>
    apiClient.post('/dinsar-results/export', { result_ids: resultIds, target_dir: targetDir }).then(r => r.data);
