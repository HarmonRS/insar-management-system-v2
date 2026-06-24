import { useCallback, useEffect, useMemo } from 'react';
import { I18nContext } from './I18nContext';

const FIXED_LANGUAGE = 'zh';

export const I18nProvider = ({ children }) => {
  const setLanguage = useCallback(() => {
    localStorage.setItem('ims_ui_language', FIXED_LANGUAGE);
  }, []);

  const t = useCallback((text) => text, []);

  const value = useMemo(() => ({
    language: FIXED_LANGUAGE,
    en: false,
    setLanguage,
    t,
  }), [setLanguage, t]);

  useEffect(() => {
    localStorage.setItem('ims_ui_language', FIXED_LANGUAGE);
    document.documentElement.setAttribute('lang', 'zh-CN');
  }, []);

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
};
