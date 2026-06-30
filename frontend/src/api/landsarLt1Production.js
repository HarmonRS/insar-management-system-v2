import apiClient from './client';

export const getLandsarLt1Capabilities = () =>
  apiClient.get('/landsar-lt1-production/capabilities').then(r => r.data);

export const previewLandsarLt1Production = payload =>
  apiClient.post('/landsar-lt1-production/preview', payload).then(r => r.data);

export const submitLandsarLt1Production = payload =>
  apiClient.post('/landsar-lt1-production/run', payload).then(r => r.data);

export const previewLandsarLt1Import = previewLandsarLt1Production;
export const submitLandsarLt1Import = submitLandsarLt1Production;

export const listLandsarLt1Products = (params = {}) =>
  apiClient.get('/landsar-lt1-production/products', { params }).then(r => r.data);

export const getLandsarLt1Product = productId =>
  apiClient.get(`/landsar-lt1-production/products/${encodeURIComponent(productId)}`).then(r => r.data);

export const getLandsarLt1AssetUrl = (productId, assetId) =>
  `/api/landsar-lt1-production/products/${encodeURIComponent(productId)}/assets/${encodeURIComponent(assetId)}`;
