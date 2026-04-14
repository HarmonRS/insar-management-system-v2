import apiClient from './client';

export const submitGeocode = (radarDataId) =>
  apiClient.post('/water/geocode', { radar_data_id: radarDataId });

export const getWaterScenes = (limit = 20, offset = 0) =>
  apiClient.get('/water/scenes', { params: { limit, offset } });

export const getWaterDoneIds = () =>
  apiClient.get('/water/scenes/done-ids').then(r => r.data.ids);

export const getWaterActiveIds = () =>
  apiClient.get('/water/scenes/active-ids').then(r => r.data.ids);

export const cleanupFailedScenes = () =>
  apiClient.delete('/water/scenes/cleanup');

export const submitFloodDetect = (preSceneId, postSceneId, refine = false) =>
  apiClient.post('/water/flood-detect', {
    pre_scene_id: preSceneId,
    post_scene_id: postSceneId,
    refine,
  });

export const getFloodEvents = () =>
  apiClient.get('/water/flood-events');

export const findWaterPairs = (params) =>
  apiClient.post('/water/find-pairs', params).then(r => r.data);

export const resetSceneStatus = (sceneId) =>
  apiClient.post(`/water/scenes/${sceneId}/reset`);

export const syncWaterScenesFromDisk = () =>
  apiClient.post('/water/sync-from-disk');

export const getFloodEventPreview = (eventId, layer) =>
  apiClient.get(`/water/flood-events/${eventId}/preview/${layer}`).then(r => r.data);
