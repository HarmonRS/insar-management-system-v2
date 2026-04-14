import apiClient from './client';

export const getPsinsarCatalogStatus = () =>
  apiClient.get('/ps-products/catalog-status').then(r => r.data);

export const queuePsinsarCatalogRebuild = payload =>
  apiClient.post('/ps-products/rebuild', payload).then(r => r.data);

export const listPsinsarProducts = (params = {}) =>
  apiClient.get('/ps-products', { params }).then(r => r.data);

export const getPsinsarProductDetail = productId =>
  apiClient.get(`/ps-products/${encodeURIComponent(productId)}`).then(r => r.data);
