import apiClient from './client';

export const getDinsarBatches = () => apiClient.get('/task-batches/dinsar').then(r => r.data);
export const getPsBatches = () => apiClient.get('/task-batches/ps').then(r => r.data);
export const createDinsarBatch = (data) => apiClient.post('/task-batches/dinsar', data).then(r => r.data);
export const createPsBatch = (data) => apiClient.post('/task-batches/ps', data).then(r => r.data);
export const updateBatchItem = (type, itemId, data) =>
    apiClient.patch(`/task-batches/${type}/items/${itemId}`, data).then(r => r.data);
export const completeBatch = (type, batchId) =>
    apiClient.patch(`/task-batches/${type}/${batchId}/complete-all`).then(r => r.data);
