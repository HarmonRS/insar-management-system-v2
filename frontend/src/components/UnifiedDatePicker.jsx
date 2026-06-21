import { useCallback, useEffect, useMemo, useRef } from 'react';
import flatpickr from 'flatpickr';
import 'flatpickr/dist/flatpickr.min.css';

const ZH_LOCALE = {
  weekdays: {
    shorthand: ['日', '一', '二', '三', '四', '五', '六'],
    longhand: ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'],
  },
  months: {
    shorthand: ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'],
    longhand: ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月'],
  },
  firstDayOfWeek: 1,
  rangeSeparator: ' 至 ',
};
const EMPTY_DATE_LIST = [];

const toIsoDateString = (dateObj) => {
  if (!(dateObj instanceof Date) || Number.isNaN(dateObj.getTime())) return '';
  const y = dateObj.getFullYear();
  const m = String(dateObj.getMonth() + 1).padStart(2, '0');
  const d = String(dateObj.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

const parseDateLikeValue = (value) => {
  const text = String(value ?? '').trim();
  if (!text) return null;

  const compact = text.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) {
    const [, y, m, d] = compact;
    return new Date(Number(y), Number(m) - 1, Number(d));
  }

  const dashed = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dashed) {
    const [, y, m, d] = dashed;
    return new Date(Number(y), Number(m) - 1, Number(d));
  }

  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
};

const installYearNavigation = (instance) => {
  const container = instance?.calendarContainer;
  if (!container || container.dataset.yearNavigationReady === '1') return;
  const monthsBar = container.querySelector('.flatpickr-months');
  if (!monthsBar) return;

  const changeYear = (delta) => {
    instance.changeYear(instance.currentYear + delta);
    instance.redraw();
  };
  const createButton = (delta, className, label, title) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `flatpickr-year-jump ${className}`;
    button.textContent = label;
    button.title = title;
    button.setAttribute('aria-label', title);
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      changeYear(delta);
    });
    return button;
  };

  const prevYearButton = createButton(-1, 'flatpickr-prev-year', '<<', '上一年');
  const nextYearButton = createButton(1, 'flatpickr-next-year', '>>', '下一年');
  const prevMonthButton = monthsBar.querySelector('.flatpickr-prev-month');
  const nextMonthButton = monthsBar.querySelector('.flatpickr-next-month');
  monthsBar.insertBefore(prevYearButton, prevMonthButton || monthsBar.firstChild);
  if (nextMonthButton?.nextSibling) {
    monthsBar.insertBefore(nextYearButton, nextMonthButton.nextSibling);
  } else {
    monthsBar.appendChild(nextYearButton);
  }
  container.dataset.yearNavigationReady = '1';
};

export default function UnifiedDatePicker({
  value,
  onChange,
  language = 'zh',
  disabled = false,
  title = '',
  ariaLabel = '',
  className = '',
  allowClear = true,
  placeholder = '',
  minDate = '',
  maxDate = '',
  enabledDates = EMPTY_DATE_LIST,
}) {
  const inputRef = useRef(null);
  const pickerRef = useRef(null);
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const selectedDate = useMemo(() => parseDateLikeValue(value), [value]);
  const parsedMinDate = useMemo(() => parseDateLikeValue(minDate), [minDate]);
  const parsedMaxDate = useMemo(() => parseDateLikeValue(maxDate), [maxDate]);

  const enabledDatesKey = useMemo(() => (
    Array.isArray(enabledDates)
      ? enabledDates
          .map((item) => String(item ?? '').trim())
          .filter(Boolean)
          .join('|')
      : ''
  ), [enabledDates]);
  const pickedEnabledDates = useMemo(() => (
    enabledDatesKey
      ? enabledDatesKey
          .split('|')
          .map(parseDateLikeValue)
          .filter((item) => item instanceof Date && !Number.isNaN(item.getTime()))
      : EMPTY_DATE_LIST
  ), [enabledDatesKey]);

  const selectedDateKey = useMemo(() => toIsoDateString(selectedDate), [selectedDate]);
  const minDateKey = useMemo(() => toIsoDateString(parsedMinDate), [parsedMinDate]);
  const maxDateKey = useMemo(() => toIsoDateString(parsedMaxDate), [parsedMaxDate]);

  const applyAltInputAttrs = useCallback((instance) => {
    if (!instance?.altInput) return;
    instance.altInput.placeholder = placeholder || '';
    instance.altInput.title = title || '';
    if (ariaLabel) {
      instance.altInput.setAttribute('aria-label', ariaLabel);
    } else {
      instance.altInput.removeAttribute('aria-label');
    }
    instance.altInput.disabled = disabled;
  }, [placeholder, title, ariaLabel, disabled]);

  useEffect(() => {
    if (!inputRef.current) return undefined;

    const pickerOptions = {
      dateFormat: 'Y-m-d',
      altInput: true,
      altInputClass: 'flatpickr-input unified-date-picker-visible-input',
      altFormat: language === 'en' ? 'Y-m-d' : 'Y年m月d日',
      locale: language === 'en' ? flatpickr.l10ns.default : ZH_LOCALE,
      allowInput: true,
      disableMobile: true,
      monthSelectorType: 'static',
      prevArrow: '<span aria-hidden="true">‹</span>',
      nextArrow: '<span aria-hidden="true">›</span>',
      clickOpens: !disabled,
      minDate: parsedMinDate || undefined,
      maxDate: parsedMaxDate || undefined,
      onReady: (_, __, fp) => {
        applyAltInputAttrs(fp);
        installYearNavigation(fp);
      },
      onOpen: (_, __, fp) => {
        applyAltInputAttrs(fp);
        installYearNavigation(fp);
      },
      onChange: (selectedDates) => {
        const nextDate = Array.isArray(selectedDates) && selectedDates.length > 0
          ? selectedDates[0]
          : null;
        onChangeRef.current?.(toIsoDateString(nextDate));
      },
    };
    if (pickedEnabledDates.length > 0) {
      pickerOptions.enable = pickedEnabledDates;
    }

    const instance = flatpickr(inputRef.current, pickerOptions);

    pickerRef.current = instance;
    applyAltInputAttrs(instance);

    return () => {
      const current = pickerRef.current;
      if (current && typeof current.destroy === 'function') {
        current.destroy();
      }
      pickerRef.current = null;
    };
  }, [
    language,
    disabled,
    parsedMinDate,
    parsedMaxDate,
    pickedEnabledDates,
    minDateKey,
    maxDateKey,
    enabledDatesKey,
    applyAltInputAttrs,
  ]);

  useEffect(() => {
    const current = pickerRef.current;
    if (!current) return;
    const currentSelectedDate = current.selectedDates && current.selectedDates.length > 0
      ? current.selectedDates[0]
      : null;
    const currentValue = toIsoDateString(currentSelectedDate);
    const nextValue = toIsoDateString(selectedDate);

    if (currentValue === nextValue) {
      applyAltInputAttrs(current);
      return;
    }

    if (selectedDate) {
      current.setDate(selectedDate, false);
    } else {
      current.clear(false);
    }
    applyAltInputAttrs(current);
  }, [selectedDate, selectedDateKey, applyAltInputAttrs]);

  return (
    <div className={`unified-date-picker ${className}`.trim()}>
      <input
        ref={inputRef}
        className="unified-date-picker-source-input"
        disabled={disabled}
        aria-hidden="true"
        tabIndex={-1}
      />
      {allowClear && value && (
        <button
          type="button"
          className="unified-date-picker-clear"
          onClick={() => onChange?.('')}
          title={language === 'en' ? 'Clear date' : '清空日期'}
          disabled={disabled}
        >
          {language === 'en' ? 'Clear' : '清空'}
        </button>
      )}
    </div>
  );
}
