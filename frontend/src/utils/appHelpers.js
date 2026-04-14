export const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => {
  switch (char) {
    case '&':
      return '&amp;';
    case '<':
      return '&lt;';
    case '>':
      return '&gt;';
    case '"':
      return '&quot;';
    case '\'':
      return '&#39;';
    default:
      return char;
  }
});

export const formatCoordinate = (value) => {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return '-';
  }
  return numericValue.toFixed(4);
};

export const normalizePagePayload = (payload, fallbackLimit, fallbackOffset) => {
  const safePayload = payload && typeof payload === 'object' ? payload : {};
  const items = Array.isArray(safePayload.items) ? safePayload.items : [];

  const parsedLimit = Number(safePayload.limit);
  const parsedOffset = Number(safePayload.offset);
  const parsedTotal = Number(safePayload.total);

  const limit = Number.isFinite(parsedLimit) ? Math.max(1, parsedLimit) : fallbackLimit;
  const offset = Number.isFinite(parsedOffset) ? Math.max(0, parsedOffset) : fallbackOffset;
  const total = Number.isFinite(parsedTotal) ? Math.max(0, parsedTotal) : items.length;
  const hasMore = typeof safePayload.has_more === 'boolean'
    ? safePayload.has_more
    : (offset + items.length) < total;

  return {
    items,
    limit,
    offset,
    total,
    hasMore,
  };
};
