import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { I18nContext } from './I18nContext';
import { SUPPORTED_LANGUAGES, translateText } from './translations';

const STORAGE_KEY = 'ims_ui_language';

const isSupportedLanguage = (value) => SUPPORTED_LANGUAGES.includes(value);

const getInitialLanguage = () => {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (isSupportedLanguage(stored)) return stored;
  return 'zh';
};

const shouldSkipNode = (node) => {
  if (!node || !node.parentElement) return true;
  const parent = node.parentElement;
  if (parent.closest('[data-no-i18n="true"]')) return true;
  const tag = parent.tagName;
  if (!tag) return true;
  return tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEXTAREA' || tag === 'CODE' || tag === 'PRE';
};

const getNodeSourceText = (textSourceMap, node) => {
  if (!textSourceMap.has(node)) {
    textSourceMap.set(node, node.nodeValue ?? '');
  }
  return textSourceMap.get(node);
};

const setNodeSourceText = (textSourceMap, node, sourceText) => {
  if (!node) return;
  textSourceMap.set(node, sourceText ?? '');
};

const getAttrSourceMap = (attrSourceMap, element) => {
  if (!attrSourceMap.has(element)) {
    attrSourceMap.set(element, new Map());
  }
  return attrSourceMap.get(element);
};

const getAttrSourceText = (attrSourceMap, element, attr, fallbackValue) => {
  const elementMap = getAttrSourceMap(attrSourceMap, element);
  if (!elementMap.has(attr)) {
    elementMap.set(attr, fallbackValue ?? '');
  }
  return elementMap.get(attr);
};

const setAttrSourceText = (attrSourceMap, element, attr, sourceText) => {
  if (!element) return;
  const elementMap = getAttrSourceMap(attrSourceMap, element);
  elementMap.set(attr, sourceText ?? '');
};

const translateElementAttrs = (element, language, attrSourceMap) => {
  if (!element || element.closest('[data-no-i18n="true"]')) return;
  ['placeholder', 'title', 'aria-label'].forEach((attr) => {
    const value = element.getAttribute(attr);
    if (!value) return;
    const source = getAttrSourceText(attrSourceMap, element, attr, value);
    const translated = translateText(source, language);
    if (translated !== value) {
      element.setAttribute(attr, translated);
    }
  });

  if (element.tagName === 'INPUT') {
    const type = (element.getAttribute('type') || '').toLowerCase();
    if (type === 'button' || type === 'submit' || type === 'reset') {
      const value = element.getAttribute('value');
      if (!value) return;
      const source = getAttrSourceText(attrSourceMap, element, 'value', value);
      const translated = translateText(source, language);
      if (translated !== value) {
        element.setAttribute('value', translated);
      }
    }
  }
};

const translateSubtree = (root, language, textSourceMap, attrSourceMap) => {
  if (!root) return;

  if (root.nodeType === Node.ELEMENT_NODE) {
    translateElementAttrs(root, language, attrSourceMap);
  }

  if (root.nodeType === Node.TEXT_NODE) {
    if (!shouldSkipNode(root)) {
      const source = getNodeSourceText(textSourceMap, root);
      const translated = translateText(source, language);
      if (translated !== root.nodeValue) {
        root.nodeValue = translated;
      }
    }
    return;
  }

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    if (!shouldSkipNode(current)) {
      const source = getNodeSourceText(textSourceMap, current);
      const translated = translateText(source, language);
      if (translated !== current.nodeValue) {
        current.nodeValue = translated;
      }
    }
    current = walker.nextNode();
  }

  if (root.querySelectorAll) {
    root.querySelectorAll('*').forEach((element) => {
      translateElementAttrs(element, language, attrSourceMap);
    });
  }
};

export const I18nProvider = ({ children }) => {
  const [language, setLanguageState] = useState(getInitialLanguage);
  const textSourceMapRef = useRef(new WeakMap());
  const attrSourceMapRef = useRef(new WeakMap());

  const setLanguage = useCallback((nextLanguage) => {
    if (!isSupportedLanguage(nextLanguage)) return;
    setLanguageState(nextLanguage);
  }, []);

  const t = useCallback((text) => translateText(text, language), [language]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, language);
    document.documentElement.setAttribute('lang', language === 'en' ? 'en-US' : 'zh-CN');
  }, [language]);

  useEffect(() => {
    if (!document?.body) return undefined;
    let isMutating = false;

    const safeTranslate = (node) => {
      if (!node) return;
      isMutating = true;
      try {
        translateSubtree(node, language, textSourceMapRef.current, attrSourceMapRef.current);
      } finally {
        isMutating = false;
      }
    };

    safeTranslate(document.body);

    const observer = new MutationObserver((mutations) => {
      if (isMutating) return;
      const roots = new Set();
      mutations.forEach((mutation) => {
        if (mutation.type === 'characterData') {
          setNodeSourceText(textSourceMapRef.current, mutation.target, mutation.target?.nodeValue ?? '');
          const parent = mutation.target?.parentElement;
          if (parent) roots.add(parent);
        }
        if (mutation.type === 'attributes' && mutation.attributeName) {
          const targetElement = mutation.target;
          const attrName = mutation.attributeName;
          const attrValue = targetElement.getAttribute(attrName);
          setAttrSourceText(attrSourceMapRef.current, targetElement, attrName, attrValue ?? '');
          roots.add(targetElement);
        }
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            roots.add(node);
          } else if (node.nodeType === Node.TEXT_NODE) {
            setNodeSourceText(textSourceMapRef.current, node, node.nodeValue ?? '');
            if (node.parentElement) {
              roots.add(node.parentElement);
            }
          } else if (node.parentElement) {
            roots.add(node.parentElement);
          }
        });
      });
      if (roots.size === 0) return;
      roots.forEach((node) => safeTranslate(node));
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['placeholder', 'title', 'aria-label', 'value'],
    });

    return () => observer.disconnect();
  }, [language]);

  const value = useMemo(() => ({
    language,
    setLanguage,
    t,
  }), [language, setLanguage, t]);

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
};
