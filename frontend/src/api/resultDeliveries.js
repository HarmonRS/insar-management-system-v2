import apiClient from './client';

export const getResultDeliveryChannels = () =>
  apiClient.get('/result-deliveries/channels').then(r => r.data);

export const createResultDelivery = payload =>
  apiClient.post('/result-deliveries', payload).then(r => r.data);

export const listResultDeliveries = (params = {}) =>
  apiClient.get('/result-deliveries', { params }).then(r => r.data);

export const getResultDelivery = deliveryId =>
  apiClient.get(`/result-deliveries/${encodeURIComponent(deliveryId)}`).then(r => r.data);

export const getResultDeliveryDownloadUrl = (deliveryId, itemId) =>
  `/api/result-deliveries/${encodeURIComponent(deliveryId)}/files/${encodeURIComponent(itemId)}/download`;

export const getResultDeliveryManifestUrl = deliveryId =>
  `/api/result-deliveries/${encodeURIComponent(deliveryId)}/manifest`;

export const getResultDeliveryArchiveUrl = deliveryId =>
  `/api/result-deliveries/${encodeURIComponent(deliveryId)}/archive/download`;
