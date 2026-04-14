import apiClient from './client';

export const getDinsarCatalogStatus = () =>
  apiClient.get('/dinsar-products/catalog-status').then(r => r.data);

export const queueDinsarProductPublish = payload =>
  apiClient.post('/dinsar-products/publish', payload).then(r => r.data);

export const queueDinsarCatalogRebuild = payload =>
  apiClient.post('/dinsar-products/rebuild', payload).then(r => r.data);

export const listDinsarProducts = (params = {}) =>
  apiClient.get('/dinsar-products', { params }).then(r => r.data);

export const getDinsarProductDetail = productId =>
  apiClient.get(`/dinsar-products/${encodeURIComponent(productId)}`).then(r => r.data);
