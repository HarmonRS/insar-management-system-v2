import { useCallback, useEffect } from 'react';
import { DEFAULT_LIST_PAGE_SIZE } from '../config/appConstants';
import { getPageInputErrorText } from '../utils/appUiHelpers';

export default function usePaginationControls({
  language,
  hasRadarSearched,
  addLog,
  radarPagination,
  dinsarPagination,
  radarPageInput,
  dinsarPageInput,
  radarPageInputTouched,
  dinsarPageInputTouched,
  setRadarPageInput,
  setRadarPageInputTouched,
  setDinsarPageInput,
  setDinsarPageInputTouched,
  setIsLoading,
  fetchAllData,
  fetchDinsarResults,
  radarSearchRequestSeqRef,
}) {
  const radarCurrentPage = Math.floor(radarPagination.offset / radarPagination.limit) + 1;
  const radarTotalPages = Math.max(1, Math.ceil(radarPagination.total / radarPagination.limit));
  const dinsarCurrentPage = Math.floor(dinsarPagination.offset / dinsarPagination.limit) + 1;
  const dinsarTotalPages = Math.max(1, Math.ceil(dinsarPagination.total / dinsarPagination.limit));

  const radarPageInputValidationError = getPageInputErrorText(radarPageInput, radarTotalPages, language);
  const dinsarPageInputValidationError = getPageInputErrorText(dinsarPageInput, dinsarTotalPages, language);
  const showRadarPageInputError = radarPageInputTouched && !!radarPageInputValidationError;
  const showDinsarPageInputError = dinsarPageInputTouched && !!dinsarPageInputValidationError;

  useEffect(() => {
    setRadarPageInput(String(radarCurrentPage));
    setRadarPageInputTouched(false);
  }, [radarCurrentPage, setRadarPageInput, setRadarPageInputTouched]);

  useEffect(() => {
    setDinsarPageInput(String(dinsarCurrentPage));
    setDinsarPageInputTouched(false);
  }, [dinsarCurrentPage, setDinsarPageInput, setDinsarPageInputTouched]);

  const handleRadarPageSizeChange = useCallback(async (event) => {
    if (!hasRadarSearched) {
      addLog('warn', '请先执行检索，再调整分页参数。');
      return;
    }
    const nextLimit = Math.max(1, Math.min(Number(event.target.value) || DEFAULT_LIST_PAGE_SIZE, 2000));
    const requestId = radarSearchRequestSeqRef.current + 1;
    radarSearchRequestSeqRef.current = requestId;
    setIsLoading(true);
    try {
      await fetchAllData({ limit: nextLimit, offset: 0, requestId });
    } finally {
      setIsLoading(false);
    }
  }, [hasRadarSearched, addLog, radarSearchRequestSeqRef, setIsLoading, fetchAllData]);

  const handleDinsarPageSizeChange = useCallback(async (event) => {
    const nextLimit = Math.max(1, Math.min(Number(event.target.value) || DEFAULT_LIST_PAGE_SIZE, 2000));
    setIsLoading(true);
    try {
      await fetchDinsarResults({ limit: nextLimit, offset: 0 });
    } finally {
      setIsLoading(false);
    }
  }, [setIsLoading, fetchDinsarResults]);

  const goToRadarPage = useCallback(async () => {
    if (!hasRadarSearched) {
      addLog('warn', '请先执行检索，再进行翻页。');
      return;
    }
    if (radarPageInputValidationError) {
      setRadarPageInputTouched(true);
      return;
    }
    const requestedPage = Number(radarPageInput);
    if (!Number.isFinite(requestedPage)) {
      setRadarPageInput(String(radarCurrentPage));
      return;
    }
    const targetPage = Math.max(1, Math.min(Math.floor(requestedPage), radarTotalPages));
    const nextOffset = (targetPage - 1) * radarPagination.limit;
    if (nextOffset === radarPagination.offset) {
      setRadarPageInput(String(targetPage));
      return;
    }
    const requestId = radarSearchRequestSeqRef.current + 1;
    radarSearchRequestSeqRef.current = requestId;
    setIsLoading(true);
    try {
      await fetchAllData({ offset: nextOffset, requestId });
    } finally {
      setIsLoading(false);
    }
  }, [
    hasRadarSearched,
    addLog,
    radarPageInputValidationError,
    setRadarPageInputTouched,
    radarPageInput,
    radarCurrentPage,
    radarTotalPages,
    radarPagination.limit,
    radarPagination.offset,
    setRadarPageInput,
    radarSearchRequestSeqRef,
    setIsLoading,
    fetchAllData,
  ]);

  const goToDinsarPage = useCallback(async () => {
    if (dinsarPageInputValidationError) {
      setDinsarPageInputTouched(true);
      return;
    }
    const requestedPage = Number(dinsarPageInput);
    if (!Number.isFinite(requestedPage)) {
      setDinsarPageInput(String(dinsarCurrentPage));
      return;
    }
    const targetPage = Math.max(1, Math.min(Math.floor(requestedPage), dinsarTotalPages));
    const nextOffset = (targetPage - 1) * dinsarPagination.limit;
    if (nextOffset === dinsarPagination.offset) {
      setDinsarPageInput(String(targetPage));
      return;
    }
    setIsLoading(true);
    try {
      await fetchDinsarResults({ offset: nextOffset });
    } finally {
      setIsLoading(false);
    }
  }, [
    dinsarPageInputValidationError,
    setDinsarPageInputTouched,
    dinsarPageInput,
    dinsarCurrentPage,
    dinsarTotalPages,
    dinsarPagination.limit,
    dinsarPagination.offset,
    setDinsarPageInput,
    setIsLoading,
    fetchDinsarResults,
  ]);

  const changeRadarPage = useCallback(async (direction) => {
    if (!hasRadarSearched) {
      addLog('warn', '请先执行检索，再进行翻页。');
      return;
    }
    const nextOffset = Math.max(0, radarPagination.offset + direction * radarPagination.limit);
    if (nextOffset === radarPagination.offset) return;
    if (direction > 0 && !radarPagination.hasMore) return;
    const requestId = radarSearchRequestSeqRef.current + 1;
    radarSearchRequestSeqRef.current = requestId;
    setIsLoading(true);
    try {
      await fetchAllData({ offset: nextOffset, requestId });
    } finally {
      setIsLoading(false);
    }
  }, [hasRadarSearched, addLog, radarPagination, radarSearchRequestSeqRef, setIsLoading, fetchAllData]);

  const changeDinsarPage = useCallback(async (direction) => {
    const nextOffset = Math.max(0, dinsarPagination.offset + direction * dinsarPagination.limit);
    if (nextOffset === dinsarPagination.offset) return;
    if (direction > 0 && !dinsarPagination.hasMore) return;
    setIsLoading(true);
    try {
      await fetchDinsarResults({ offset: nextOffset });
    } finally {
      setIsLoading(false);
    }
  }, [dinsarPagination, setIsLoading, fetchDinsarResults]);

  return {
    radarCurrentPage,
    radarTotalPages,
    dinsarCurrentPage,
    dinsarTotalPages,
    radarPageInputValidationError,
    dinsarPageInputValidationError,
    showRadarPageInputError,
    showDinsarPageInputError,
    handleRadarPageSizeChange,
    handleDinsarPageSizeChange,
    goToRadarPage,
    goToDinsarPage,
    changeRadarPage,
    changeDinsarPage,
  };
}
