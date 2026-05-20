import apiClient from './client';

export const submitFloodPreprocess = (payload) =>
  apiClient.post('/flood/preprocess', payload);

export const getFloodScenes = (limit = 20, offset = 0) =>
  apiClient.get('/flood/scenes', { params: { limit, offset } });

export const getFloodDoneRadarIds = () =>
  apiClient.get('/flood/scenes/done-radar-ids').then(r => r.data.ids);

export const getFloodActiveRadarIds = () =>
  apiClient.get('/flood/scenes/active-radar-ids').then(r => r.data.ids);

export const resetFloodScene = (sceneId) =>
  apiClient.post(`/flood/scenes/${sceneId}/reset`);

export const submitFloodWaterExtraction = (payload) =>
  apiClient.post('/flood/water-extractions', payload);

export const getFloodWaterExtractions = (limit = 20, offset = 0, status = null) =>
  apiClient.get('/flood/water-extractions', { params: { limit, offset, ...(status ? { status } : {}) } });

export const getFloodWaterExtractionPreview = (id) =>
  apiClient.get(`/flood/water-extractions/${id}/preview`).then(r => r.data);

export const searchFloodPairs = (payload) =>
  apiClient.post('/flood/pairs/search', payload).then(r => r.data);

export const searchFloodDisasterPairs = (payload) =>
  apiClient.post('/flood/disaster-pairs/search', payload).then(r => r.data);

export const submitFloodDetection = (payload) =>
  apiClient.post('/flood/detections', payload);

export const getFloodDetections = (params = {}) =>
  apiClient.get('/flood/detections', { params });

export const getFloodDetectionPreview = (id, layer) =>
  apiClient.get(`/flood/detections/${id}/preview/${layer}`).then(r => r.data);

export const runFloodOverlay = (id, payload = {}) =>
  apiClient.post(`/flood/detections/${id}/overlay`, payload);

export const getFloodImpact = (id) =>
  apiClient.get(`/flood/detections/${id}/impact`).then(r => r.data);

export const createFloodProduct = (detectionId) =>
  apiClient.post(`/flood/detections/${detectionId}/products`);

export const getFloodProducts = (params = {}) =>
  apiClient.get('/flood/products', { params });

export const getFloodProduct = (id) =>
  apiClient.get(`/flood/products/${id}`);

export const getFloodProductManifest = (id) =>
  apiClient.get(`/flood/products/${id}/manifest`).then(r => r.data);

export const getFloodResults = (params = {}) =>
  apiClient.get('/flood/results', { params });

export const getFloodResult = (id) =>
  apiClient.get(`/flood/results/${id}`);

export const getFloodResultManifest = (id) =>
  apiClient.get(`/flood/results/${id}/manifest`).then(r => r.data);
