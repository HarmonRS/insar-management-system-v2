import apiClient from './client';

export const getFloodSources = (params = {}) =>
  apiClient.get('/flood/sources', { params });

export const refreshFloodSources = () =>
  apiClient.post('/flood/sources/refresh');

export const getFloodSourceReadiness = (id) =>
  apiClient.get(`/flood/sources/${id}/readiness`);

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

export const getFloodPreprocessRuns = (params = {}) =>
  apiClient.get('/flood/preprocess-runs', { params });

export const getFloodReadyProducts = (params = {}) =>
  apiClient.get('/flood/ready-products', { params });

export const getFloodReadyProductPreview = (id) =>
  apiClient.get(`/flood/ready-products/${id}/preview`).then(r => r.data);

export const searchFloodPairs = (payload) =>
  apiClient.post('/flood/pairs/search', payload);

export const saveFloodPair = (payload) =>
  apiClient.post('/flood/pairs', payload);

export const getFloodPairs = (params = {}) =>
  apiClient.get('/flood/pairs', { params });

export const deleteFloodPair = (id) =>
  apiClient.delete(`/flood/pairs/${id}`);

export const submitFloodDetection = (payload) =>
  apiClient.post('/flood/detections', payload);

export const getFloodDetections = (params = {}) =>
  apiClient.get('/flood/detections', { params });

export const getFloodDetection = (id) =>
  apiClient.get(`/flood/detections/${id}`);

export const getFloodDetectionPreview = (id, layer) =>
  apiClient.get(`/flood/detections/${id}/preview/${layer}`).then(r => r.data);

export const vectorizeFloodDetection = (id, payload = {}) =>
  apiClient.post(`/flood/detections/${id}/vectorize`, payload);

export const runFloodOverlay = (id, payload = {}) =>
  apiClient.post(`/flood/detections/${id}/overlay`, payload);

export const getFloodImpact = (id) =>
  apiClient.get(`/flood/detections/${id}/impact`).then(r => r.data);

export const getFloodResults = (params = {}) =>
  apiClient.get('/flood/results', { params });

export const getFloodResult = (id) =>
  apiClient.get(`/flood/results/${id}`);

export const getFloodResultManifest = (id) =>
  apiClient.get(`/flood/results/${id}/manifest`).then(r => r.data);

export const createFloodReport = (payload) =>
  apiClient.post('/flood/reports', payload);

export const getFloodReport = (id) =>
  apiClient.get(`/flood/reports/${id}`).then(r => r.data);
