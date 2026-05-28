import apiClient from './client';

export const getSbasInsarCatalogStatus = () =>
  apiClient.get('/sbas-insar-products/catalog-status').then(r => r.data);

export const queueSbasInsarCatalogRebuild = payload =>
  apiClient.post('/sbas-insar-products/rebuild', payload).then(r => r.data);

export const listSbasInsarProducts = (params = {}) =>
  apiClient.get('/sbas-insar-products', { params }).then(r => r.data);

export const getSbasInsarProductDetail = productId =>
  apiClient.get(`/sbas-insar-products/${encodeURIComponent(productId)}`).then(r => r.data);

export const getSbasInsarProductPreviewUrl = productId =>
  `${apiClient.defaults.baseURL || '/api'}/sbas-insar-products/${encodeURIComponent(productId)}/preview`;

export const getSbasInsarProductAssetUrl = (productId, assetId) =>
  `${apiClient.defaults.baseURL || '/api'}/sbas-insar-products/${encodeURIComponent(productId)}/assets/${encodeURIComponent(assetId)}`;
