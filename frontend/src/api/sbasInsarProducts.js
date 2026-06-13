import apiClient from './client';

export const getSbasInsarCatalogStatus = () =>
  apiClient.get('/sbas-insar-products/catalog-status').then(r => r.data);

export const queueSbasInsarCatalogRebuild = payload =>
  apiClient.post('/sbas-insar-products/rebuild', payload).then(r => r.data);

export const listSbasInsarProducts = (params = {}) =>
  apiClient.get('/sbas-insar-products', { params }).then(r => r.data);

export const getSbasInsarProductDetail = productId =>
  apiClient.get(`/sbas-insar-products/${encodeURIComponent(productId)}`).then(r => r.data);

export const querySbasInsarPointTimeseries = (productId, payload) =>
  apiClient.post(`/sbas-insar-products/${encodeURIComponent(productId)}/point-timeseries`, payload).then(r => r.data);

const appendCacheKey = (url, cacheKey) =>
  cacheKey ? `${url}?v=${encodeURIComponent(cacheKey)}` : url;

export const getSbasInsarProductPreviewUrl = (productId, cacheKey = '') =>
  appendCacheKey(
    `${apiClient.defaults.baseURL || '/api'}/sbas-insar-products/${encodeURIComponent(productId)}/preview`,
    cacheKey,
  );

export const getSbasInsarProductAssetUrl = (productId, assetId, cacheKey = '') =>
  appendCacheKey(
    `${apiClient.defaults.baseURL || '/api'}/sbas-insar-products/${encodeURIComponent(productId)}/assets/${encodeURIComponent(assetId)}`,
    cacheKey,
  );
