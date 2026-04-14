import apiClient from './client';

// ============ 水体检测 API ============

export const submitWaterDetect = ({ scene_id, input_path }) =>
  apiClient.post('/water/detect', { scene_id, input_path });

export const getWaterDetections = (limit = 20, offset = 0, status = null) =>
  apiClient.get('/water/detections', { params: { limit, offset, ...(status ? { status } : {}) } });

export const getWaterDetection = (id) =>
  apiClient.get(`/water/detections/${id}`);

export const getWaterDetectionPreview = (id) =>
  apiClient.get(`/water/detections/${id}/preview`).then(r => r.data);

// ============ GF3 处理 API ============

export const submitGF3Process = ({ input_dir, resolution }) =>
  apiClient.post('/water/gf3-process', { input_dir, resolution });

export const getGF3Results = (limit = 20, offset = 0) =>
  apiClient.get('/water/gf3-results', { params: { limit, offset } });

export const getGF3Result = (id) =>
  apiClient.get(`/water/gf3-results/${id}`);

// ============ GF3 批量处理 API（DataMonitorPanel 使用）============

export const runGf3BatchProcess = () =>
  apiClient.post('/monitor/gf3-process');
