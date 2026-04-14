import { useCallback, useEffect } from 'react';
import apiClient from '../api/client';
import { getHealth } from '../api/health';

const HEALTH_POLL_INTERVAL_MS = 30000;

export default function useAppAuthLifecycle({
  ensureCanOperate,
  clearRadarSearchResults,
  radarSearchRequestSeqRef,
  prevLicenseOkRef,
  aoeLayerRef,
  activeLayersRef,
  radarPreviewLayersRef,
  setHasRadarSearched,
  setCurrentUser,
  setAuthChecked,
  setIsGlobalLocked,
  setPendingTaskIds,
  setLicenseLoading,
  setLicenseStatus,
  setHealthLoading,
  setHealthError,
  setHealthStatus,
  setLicenseFileName,
  setLicenseUploadStatus,
  setAoiLayer,
  setAllData,
  setRadarPagination,
}) {
  const fetchCurrentUser = useCallback(async () => {
    try {
      const response = await apiClient.get('/auth/me');
      setCurrentUser(response.data || null);
    } catch {
      setCurrentUser(null);
    } finally {
      setAuthChecked(true);
    }
  }, [setCurrentUser, setAuthChecked]);

  const handleLoginSuccess = useCallback(async () => {
    await fetchCurrentUser();
  }, [fetchCurrentUser]);

  const handleLogout = useCallback(async () => {
    try {
      await apiClient.post('/auth/logout');
    } catch (error) {
      console.error('Logout failed:', error);
    } finally {
      radarSearchRequestSeqRef.current += 1;
      setHasRadarSearched(false);
      clearRadarSearchResults();
      setCurrentUser(null);
      setIsGlobalLocked(false);
      setPendingTaskIds([]);
      prevLicenseOkRef.current = false;
      setAuthChecked(true);
    }
  }, [
    clearRadarSearchResults,
    radarSearchRequestSeqRef,
    setHasRadarSearched,
    setCurrentUser,
    setIsGlobalLocked,
    setPendingTaskIds,
    prevLicenseOkRef,
    setAuthChecked,
  ]);

  const fetchLicenseStatus = useCallback(async () => {
    try {
      setLicenseLoading(true);
      const response = await apiClient.get('/license/status');
      const data = response.data || {};
      if (!data.ok && !data.reason) {
        data.reason = '未授权';
      }
      setLicenseStatus(data);
    } catch (error) {
      setLicenseStatus({
        ok: false,
        reason: error.response?.data?.detail || '无法获取授权状态',
      });
    } finally {
      setLicenseLoading(false);
    }
  }, [setLicenseLoading, setLicenseStatus]);

  const fetchHealthStatus = useCallback(async (options = {}) => {
    const { refresh = false, silent = false } = options;
    try {
      if (!silent) {
        setHealthLoading(true);
      }
      setHealthError('');
      const data = await getHealth(refresh ? { refresh: true } : {});
      setHealthStatus(data || null);
    } catch (error) {
      setHealthError(error.response?.data?.detail || '运维自检失败');
      setHealthStatus(null);
    } finally {
      if (!silent) {
        setHealthLoading(false);
      }
    }
  }, [setHealthLoading, setHealthError, setHealthStatus]);

  const handleLicenseUpload = useCallback(async (file) => {
    if (!file) return;
    if (!ensureCanOperate()) return;
    try {
      setLicenseFileName(file.name);
      setLicenseUploadStatus({ type: 'info', message: '正在上传授权文件...' });
      const form = new FormData();
      form.append('file', file);
      const response = await apiClient.post('/license/upload', form);
      setLicenseUploadStatus({ type: 'success', message: response.data?.message || '授权文件已上传' });
      await fetchLicenseStatus();
    } catch (error) {
      setLicenseUploadStatus({ type: 'error', message: error.response?.data?.detail || '授权文件上传失败' });
    }
  }, [ensureCanOperate, setLicenseFileName, setLicenseUploadStatus, fetchLicenseStatus]);

  useEffect(() => {
    const interceptorId = apiClient.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error?.response?.status === 401) {
          radarSearchRequestSeqRef.current += 1;
          setHasRadarSearched(false);
          if (aoeLayerRef.current) {
            aoeLayerRef.current.remove();
            aoeLayerRef.current = null;
          }
          setAoiLayer(null);
          Object.values(activeLayersRef.current).forEach((layer) => layer.remove());
          activeLayersRef.current = {};
          Object.values(radarPreviewLayersRef.current).forEach((layer) => layer.remove());
          radarPreviewLayersRef.current = {};
          setAllData([]);
          setRadarPagination((prev) => ({
            ...prev,
            total: 0,
            offset: 0,
            hasMore: false,
          }));
          setCurrentUser(null);
          setAuthChecked(true);
          prevLicenseOkRef.current = false;
        }
        return Promise.reject(error);
      }
    );
    return () => {
      apiClient.interceptors.response.eject(interceptorId);
    };
  }, [
    radarSearchRequestSeqRef,
    setHasRadarSearched,
    aoeLayerRef,
    setAoiLayer,
    activeLayersRef,
    radarPreviewLayersRef,
    setAllData,
    setRadarPagination,
    setCurrentUser,
    setAuthChecked,
    prevLicenseOkRef,
  ]);

  useEffect(() => {
    fetchCurrentUser();
    fetchLicenseStatus();
  }, [fetchCurrentUser, fetchLicenseStatus]);

  useEffect(() => {
    void fetchHealthStatus();
    const interval = setInterval(() => {
      void fetchHealthStatus({ silent: true });
    }, HEALTH_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchHealthStatus]);

  return {
    handleLoginSuccess,
    handleLogout,
    fetchLicenseStatus,
    fetchHealthStatus,
    handleLicenseUpload,
  };
}
