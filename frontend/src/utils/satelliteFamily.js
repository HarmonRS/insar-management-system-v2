export function normalizeSatelliteFamily(value) {
    const raw = String(value || '').trim().toUpperCase();
    if (!raw) return '';
    const compact = raw.replace(/[-_\s]+/g, '');
    if (['LT1', 'LT1A', 'LT1B', 'LUTAN1', 'LUTAN1A', 'LUTAN1B'].includes(compact)) return 'LT1';
    if (['S1', 'S1A', 'S1B', 'S1C', 'SENTINEL1', 'SENTINEL1A', 'SENTINEL1B', 'SENTINEL1C'].includes(compact)) return 'S1';
    if (['GF3', 'GAOFEN3'].includes(compact)) return 'GF3';
    return raw;
}

export function inferSatelliteFamilyFromResultLike(item) {
    const direct = normalizeSatelliteFamily(
        item?.satellite_family
        || item?.master_satellite
        || item?.slave_satellite
        || item?.satellite
    );
    if (direct) return direct;

    const pairKey = String(item?.pair_key || '').trim().toLowerCase();
    if (pairKey.startsWith('s1_')) return 'S1';
    if (pairKey.startsWith('lt1_')) return 'LT1';
    if (pairKey.startsWith('gf3_')) return 'GF3';
    return '';
}

export function formatSatelliteFamilyLabel(value) {
    const family = normalizeSatelliteFamily(value);
    if (family === 'S1') return 'Sentinel-1';
    if (family === 'LT1') return 'LT-1';
    if (family === 'GF3') return 'GF3';
    return family || '-';
}
