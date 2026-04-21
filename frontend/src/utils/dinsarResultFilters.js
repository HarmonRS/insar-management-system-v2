import { DINSAR_ENGINE_ALL } from './dinsarEngines';

export const DINSAR_STRATEGY_ALL = '__ALL__';

function normalizeTraceSearch(value) {
    return String(value ?? '').trim().toLowerCase();
}

function hasAiScore(result) {
    return result?.ai_score !== null && result?.ai_score !== undefined && result?.ai_score !== '';
}

function buildTraceText(result = {}) {
    return [
        result.name,
        result.task_alias,
        result.task_name,
        result.pair_key,
        result.pair_uid,
        result.run_key,
        result.network_run_id,
        result.network_edge_id,
        result.policy_version,
        result.selection_strategy,
        result.engine_code,
    ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
}

export function buildDinsarStrategyOptions(results = []) {
    const values = new Set();
    results.forEach((result) => {
        const value = String(result?.selection_strategy || '').trim();
        if (value) {
            values.add(value);
        }
    });
    return [DINSAR_STRATEGY_ALL, ...Array.from(values).sort()];
}

export function matchesFocusedHazardPoint(result, focusedHazardPoint) {
    if (!focusedHazardPoint) {
        return true;
    }

    const longitude = Number(focusedHazardPoint.longitude);
    const latitude = Number(focusedHazardPoint.latitude);
    const minLon = Number(result?.min_lon);
    const maxLon = Number(result?.max_lon);
    const minLat = Number(result?.min_lat);
    const maxLat = Number(result?.max_lat);

    if (
        !Number.isFinite(longitude) ||
        !Number.isFinite(latitude) ||
        !Number.isFinite(minLon) ||
        !Number.isFinite(maxLon) ||
        !Number.isFinite(minLat) ||
        !Number.isFinite(maxLat)
    ) {
        return false;
    }

    return (
        longitude >= minLon &&
        longitude <= maxLon &&
        latitude >= minLat &&
        latitude <= maxLat
    );
}

export function matchesDinsarResultFilters(
    result,
    {
        scoreFilter = 0,
        engineFilter = DINSAR_ENGINE_ALL,
        strategyFilter = DINSAR_STRATEGY_ALL,
        traceSearch = '',
        focusedHazardPoint = null,
    } = {}
) {
    const matchesScore = !hasAiScore(result) || Number(result.ai_score) >= Number(scoreFilter || 0);
    const engineValue = String(result?.engine_code || '').trim().toLowerCase();
    const matchesEngine = engineFilter === DINSAR_ENGINE_ALL || engineValue === engineFilter;
    const strategyValue = String(result?.selection_strategy || '').trim();
    const matchesStrategy = strategyFilter === DINSAR_STRATEGY_ALL || strategyValue === strategyFilter;
    const normalizedTraceSearch = normalizeTraceSearch(traceSearch);
    const matchesTrace = !normalizedTraceSearch || buildTraceText(result).includes(normalizedTraceSearch);
    const matchesHazard = matchesFocusedHazardPoint(result, focusedHazardPoint);

    return matchesScore && matchesEngine && matchesStrategy && matchesTrace && matchesHazard;
}

export function filterDinsarResults(results = [], filters = {}) {
    return (Array.isArray(results) ? results : []).filter((result) => matchesDinsarResultFilters(result, filters));
}
