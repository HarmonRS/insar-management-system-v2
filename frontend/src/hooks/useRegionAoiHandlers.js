import { useCallback } from 'react';
import L from 'leaflet';
import apiClient from '../api/client';
import { getRegionDisplayName, getSelectedRegionTreeId } from '../utils/appUiHelpers';

export default function useRegionAoiHandlers({
  setPairingRegionLoading,
  setPairingRegionError,
  setPairingRegionOptions,
  setPsRegionLoading,
  setPsRegionError,
  setPsRegionOptions,
  setPairingAoiMode,
  pairingRegionOptions,
  setPsAoiMode,
  psRegionOptions,
  setPairingRegionSelection,
  setPsRegionSelection,
  setMapRegionLoading,
  setMapRegionError,
  setMapRegionOptions,
  showMapRegionLocator,
  setShowMapRegionLocator,
  mapRegionOptions,
  setMapRegionSelection,
  setMapRegionLocatedName,
  setMapRegionLocating,
  setRadarSearchRegionLoading,
  setRadarSearchRegionError,
  setRadarSearchRegionOptions,
  setRadarSearchAoiMode,
  setRadarSearchFiles,
  setRadarSearchRegionSelection,
  radarSearchRegionOptions,
  setRadarSearchDraft,
  mapRef,
  mapRegionLayerRef,
  mapRegionSelection,
  addLog,
  pairingAoiMode,
  setShowPairingModal,
  psAoiMode,
  setShowPsModal,
}) {
  const fetchRegionChildren = useCallback(async (parentTreeId = '1') => {
    const response = await apiClient.get('/aoi/regions/children', {
      params: { parent_tree_id: parentTreeId },
    });
    return response.data?.children || [];
  }, []);

  const fetchRegionGeometry = useCallback(async (treeId) => {
    const response = await apiClient.get(`/aoi/regions/${treeId}/geometry`);
    return response.data?.aoi_geojson || null;
  }, []);

  const loadPairingProvinces = useCallback(async () => {
    setPairingRegionLoading(true);
    setPairingRegionError('');
    try {
      const provinces = await fetchRegionChildren('1');
      setPairingRegionOptions({ provinces, cities: [] });
    } catch (error) {
      setPairingRegionError(error.response?.data?.detail || error.message || '行政区加载失败');
      setPairingRegionOptions({ provinces: [], cities: [] });
    } finally {
      setPairingRegionLoading(false);
    }
  }, [fetchRegionChildren, setPairingRegionLoading, setPairingRegionError, setPairingRegionOptions]);

  const loadPsProvinces = useCallback(async () => {
    setPsRegionLoading(true);
    setPsRegionError('');
    try {
      const provinces = await fetchRegionChildren('1');
      setPsRegionOptions({ provinces, cities: [] });
    } catch (error) {
      setPsRegionError(error.response?.data?.detail || error.message || '行政区加载失败');
      setPsRegionOptions({ provinces: [], cities: [] });
    } finally {
      setPsRegionLoading(false);
    }
  }, [fetchRegionChildren, setPsRegionLoading, setPsRegionError, setPsRegionOptions]);

  const handlePairingAoiModeChange = useCallback(async (nextMode) => {
    setPairingAoiMode(nextMode);
    if (nextMode === 'region' && pairingRegionOptions.provinces.length === 0) {
      await loadPairingProvinces();
    }
  }, [setPairingAoiMode, pairingRegionOptions.provinces.length, loadPairingProvinces]);

  const handlePsAoiModeChange = useCallback(async (nextMode) => {
    setPsAoiMode(nextMode);
    if (nextMode === 'region' && psRegionOptions.provinces.length === 0) {
      await loadPsProvinces();
    }
  }, [setPsAoiMode, psRegionOptions.provinces.length, loadPsProvinces]);

  const handlePairingProvinceChange = useCallback(async (provinceId) => {
    setPairingRegionSelection({ province: provinceId, city: '' });
    setPairingRegionOptions((prev) => ({ ...prev, cities: [] }));
    if (!provinceId) return;

    setPairingRegionLoading(true);
    setPairingRegionError('');
    try {
      const cities = await fetchRegionChildren(provinceId);
      setPairingRegionOptions((prev) => ({ ...prev, cities }));
    } catch (error) {
      setPairingRegionError(error.response?.data?.detail || error.message || '地市加载失败');
    } finally {
      setPairingRegionLoading(false);
    }
  }, [
    fetchRegionChildren,
    setPairingRegionSelection,
    setPairingRegionOptions,
    setPairingRegionLoading,
    setPairingRegionError,
  ]);

  const handlePairingCityChange = useCallback((cityId) => {
    setPairingRegionSelection((prev) => ({ ...prev, city: cityId }));
  }, [setPairingRegionSelection]);

  const handlePsProvinceChange = useCallback(async (provinceId) => {
    setPsRegionSelection({ province: provinceId, city: '' });
    setPsRegionOptions((prev) => ({ ...prev, cities: [] }));
    if (!provinceId) return;

    setPsRegionLoading(true);
    setPsRegionError('');
    try {
      const cities = await fetchRegionChildren(provinceId);
      setPsRegionOptions((prev) => ({ ...prev, cities }));
    } catch (error) {
      setPsRegionError(error.response?.data?.detail || error.message || '地市加载失败');
    } finally {
      setPsRegionLoading(false);
    }
  }, [
    fetchRegionChildren,
    setPsRegionSelection,
    setPsRegionOptions,
    setPsRegionLoading,
    setPsRegionError,
  ]);

  const handlePsCityChange = useCallback((cityId) => {
    setPsRegionSelection((prev) => ({ ...prev, city: cityId }));
  }, [setPsRegionSelection]);

  const loadMapRegionProvinces = useCallback(async () => {
    setMapRegionLoading(true);
    setMapRegionError('');
    try {
      const provinces = await fetchRegionChildren('1');
      setMapRegionOptions({ provinces, cities: [] });
    } catch (error) {
      setMapRegionError(error.response?.data?.detail || error.message || '行政区加载失败');
      setMapRegionOptions({ provinces: [], cities: [] });
    } finally {
      setMapRegionLoading(false);
    }
  }, [fetchRegionChildren, setMapRegionLoading, setMapRegionError, setMapRegionOptions]);

  const toggleMapRegionLocator = useCallback(async () => {
    const nextVisible = !showMapRegionLocator;
    setShowMapRegionLocator(nextVisible);
    setMapRegionError('');
    if (nextVisible && mapRegionOptions.provinces.length === 0) {
      await loadMapRegionProvinces();
    }
  }, [
    showMapRegionLocator,
    setShowMapRegionLocator,
    setMapRegionError,
    mapRegionOptions.provinces.length,
    loadMapRegionProvinces,
  ]);

  const handleMapRegionProvinceChange = useCallback(async (provinceId) => {
    setMapRegionSelection({ province: provinceId, city: '' });
    setMapRegionOptions((prev) => ({ ...prev, cities: [] }));
    setMapRegionLocatedName('');
    if (!provinceId) return;

    setMapRegionLoading(true);
    setMapRegionError('');
    try {
      const cities = await fetchRegionChildren(provinceId);
      setMapRegionOptions((prev) => ({ ...prev, cities }));
    } catch (error) {
      setMapRegionError(error.response?.data?.detail || error.message || '地市加载失败');
    } finally {
      setMapRegionLoading(false);
    }
  }, [
    fetchRegionChildren,
    setMapRegionSelection,
    setMapRegionOptions,
    setMapRegionLocatedName,
    setMapRegionLoading,
    setMapRegionError,
  ]);

  const handleMapRegionCityChange = useCallback((cityId) => {
    setMapRegionSelection((prev) => ({ ...prev, city: cityId }));
    setMapRegionLocatedName('');
  }, [setMapRegionSelection, setMapRegionLocatedName]);

  const loadRadarSearchProvinces = useCallback(async () => {
    setRadarSearchRegionLoading(true);
    setRadarSearchRegionError('');
    try {
      const provinces = await fetchRegionChildren('1');
      setRadarSearchRegionOptions({ provinces, cities: [] });
    } catch (error) {
      setRadarSearchRegionError(error.response?.data?.detail || error.message || '行政区加载失败');
      setRadarSearchRegionOptions({ provinces: [], cities: [] });
    } finally {
      setRadarSearchRegionLoading(false);
    }
  }, [
    fetchRegionChildren,
    setRadarSearchRegionLoading,
    setRadarSearchRegionError,
    setRadarSearchRegionOptions,
  ]);

  const handleRadarSearchAoiModeChange = useCallback(async (nextMode) => {
    setRadarSearchAoiMode(nextMode);
    setRadarSearchRegionError('');
    if (nextMode !== 'shp') {
      setRadarSearchFiles(null);
    }
    if (nextMode !== 'region') {
      setRadarSearchRegionSelection({ province: '', city: '' });
      setRadarSearchRegionOptions({ provinces: [], cities: [] });
    } else if (radarSearchRegionOptions.provinces.length === 0) {
      await loadRadarSearchProvinces();
    }
  }, [
    setRadarSearchAoiMode,
    setRadarSearchRegionError,
    setRadarSearchFiles,
    setRadarSearchRegionSelection,
    setRadarSearchRegionOptions,
    radarSearchRegionOptions.provinces.length,
    loadRadarSearchProvinces,
  ]);

  const handleRadarSearchProvinceChange = useCallback(async (provinceId) => {
    setRadarSearchRegionSelection({ province: provinceId, city: '' });
    setRadarSearchRegionOptions((prev) => ({ ...prev, cities: [] }));
    if (!provinceId) return;

    setRadarSearchRegionLoading(true);
    setRadarSearchRegionError('');
    try {
      const cities = await fetchRegionChildren(provinceId);
      setRadarSearchRegionOptions((prev) => ({ ...prev, cities }));
    } catch (error) {
      setRadarSearchRegionError(error.response?.data?.detail || error.message || '地市加载失败');
    } finally {
      setRadarSearchRegionLoading(false);
    }
  }, [
    fetchRegionChildren,
    setRadarSearchRegionSelection,
    setRadarSearchRegionOptions,
    setRadarSearchRegionLoading,
    setRadarSearchRegionError,
  ]);

  const handleRadarSearchCityChange = useCallback((cityId) => {
    setRadarSearchRegionSelection((prev) => ({ ...prev, city: cityId }));
  }, [setRadarSearchRegionSelection]);

  const updateRadarSearchDraft = useCallback((field, value) => {
    setRadarSearchDraft((prev) => ({ ...prev, [field]: value }));
  }, [setRadarSearchDraft]);

  const locateSelectedRegionOnMap = useCallback(async () => {
    const selectedRegionTreeId = getSelectedRegionTreeId(mapRegionSelection);
    if (!selectedRegionTreeId) {
      setMapRegionError('请先选择要定位的行政区。');
      return;
    }

    setMapRegionLocating(true);
    setMapRegionError('');
    try {
      if (!mapRef.current) {
        throw new Error('地图未初始化');
      }
      const selectedAoiGeoJson = await fetchRegionGeometry(selectedRegionTreeId);
      if (!selectedAoiGeoJson) {
        throw new Error('未获取到行政区边界，请检查后端行政区边界数据。');
      }

      if (mapRegionLayerRef.current) {
        mapRegionLayerRef.current.remove();
        mapRegionLayerRef.current = null;
      }

      const regionLayer = L.geoJSON(selectedAoiGeoJson, {
        style: {
          color: '#2563eb',
          weight: 2,
          opacity: 0.95,
          fillColor: '#60a5fa',
          fillOpacity: 0.08,
        },
      }).addTo(mapRef.current);
      mapRegionLayerRef.current = regionLayer;

      const bounds = regionLayer.getBounds();
      if (bounds?.isValid()) {
        mapRef.current.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });
      }
      const selectedRegionName = getRegionDisplayName(mapRegionSelection, mapRegionOptions) || selectedRegionTreeId;
      setMapRegionLocatedName(selectedRegionName);
      addLog('info', `地图区域定位成功: ${selectedRegionName}`);
    } catch (error) {
      const errorMessage = error.response?.data?.detail || error.message || '地图定位失败';
      setMapRegionError(errorMessage);
      addLog('error', `地图定位失败: ${errorMessage}`);
    } finally {
      setMapRegionLocating(false);
    }
  }, [
    mapRegionSelection,
    setMapRegionError,
    setMapRegionLocating,
    fetchRegionGeometry,
    mapRef,
    mapRegionLayerRef,
    mapRegionOptions,
    setMapRegionLocatedName,
    addLog,
  ]);

  const clearMapRegionHighlight = useCallback(() => {
    if (mapRegionLayerRef.current) {
      mapRegionLayerRef.current.remove();
      mapRegionLayerRef.current = null;
    }
    setMapRegionLocatedName('');
    setMapRegionError('');
    addLog('info', '已清除地图定位高亮。');
  }, [mapRegionLayerRef, setMapRegionLocatedName, setMapRegionError, addLog]);

  const openPairingModal = useCallback(async () => {
    setShowPairingModal(true);
    if (pairingAoiMode === 'region' && pairingRegionOptions.provinces.length === 0) {
      await loadPairingProvinces();
    }
  }, [setShowPairingModal, pairingAoiMode, pairingRegionOptions.provinces.length, loadPairingProvinces]);

  const openPsModal = useCallback(async () => {
    setShowPsModal(true);
    if (psAoiMode === 'region' && psRegionOptions.provinces.length === 0) {
      await loadPsProvinces();
    }
  }, [setShowPsModal, psAoiMode, psRegionOptions.provinces.length, loadPsProvinces]);

  return {
    fetchRegionGeometry,
    handlePairingAoiModeChange,
    handlePsAoiModeChange,
    handlePairingProvinceChange,
    handlePairingCityChange,
    handlePsProvinceChange,
    handlePsCityChange,
    toggleMapRegionLocator,
    handleMapRegionProvinceChange,
    handleMapRegionCityChange,
    handleRadarSearchAoiModeChange,
    handleRadarSearchProvinceChange,
    handleRadarSearchCityChange,
    updateRadarSearchDraft,
    locateSelectedRegionOnMap,
    clearMapRegionHighlight,
    openPairingModal,
    openPsModal,
  };
}
