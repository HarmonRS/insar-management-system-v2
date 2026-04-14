import apiClient from './client';

export const getRegionChildren = (parentTreeId) =>
    apiClient.get('/aoi/regions/children', { params: { parent_tree_id: parentTreeId } }).then(r => r.data);

export const getRegionGeometry = (treeId) =>
    apiClient.get(`/aoi/regions/${treeId}/geometry`).then(r => r.data);
