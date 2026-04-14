import apiClient from './client';

export const getAiStatus = () => apiClient.get('/ai/status').then(r => r.data);
export const trainModel = () => apiClient.post('/ai/train').then(r => r.data);
export const predictAll = () => apiClient.post('/ai/predict-all').then(r => r.data);
export const analyzeResult = (resultId) => apiClient.post(`/ai/analyze-result/${resultId}`).then(r => r.data);
export const analyzeMap = (data) => apiClient.post('/ai/analyze-map', data).then(r => r.data);
export const warmupAi = () => apiClient.post('/ai/warmup').then(r => r.data);

// ============ 新版 AI 诊断 API ============

/**
 * 获取可用的 Prompt 模板列表
 */
export const getPromptTemplates = () =>
  apiClient.get('/ai/prompt-templates').then(r => r.data);

/**
 * 创建 AI 诊断任务
 * @param {Object} data - { result_id, model_name, prompt_template, custom_prompt? }
 */
export const createDiagnosis = (data) =>
  apiClient.post('/ai/diagnosis', data).then(r => r.data);

/**
 * 查询 AI 诊断列表
 * @param {Object} params - { result_id?, task_id?, risk_level?, page?, page_size? }
 */
export const listDiagnoses = (params = {}) =>
  apiClient.get('/ai/diagnosis', { params }).then(r => r.data);

/**
 * 获取单个 AI 诊断详情
 * @param {number} diagnosisId
 */
export const getDiagnosis = (diagnosisId) =>
  apiClient.get(`/ai/diagnosis/${diagnosisId}`).then(r => r.data);

/**
 * 删除 AI 诊断记录
 * @param {number} diagnosisId
 */
export const deleteDiagnosis = (diagnosisId) =>
  apiClient.delete(`/ai/diagnosis/${diagnosisId}`);

