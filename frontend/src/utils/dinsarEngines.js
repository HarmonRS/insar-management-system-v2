export const DINSAR_ENGINE_ALL = '__ALL__';

export const KNOWN_DINSAR_ENGINE_CODES = ['sarscape', 'envi', 'isce2', 'pyint', 'landsar'];

const DINSAR_ENGINE_META = {
    sarscape: {
        label: 'ENVI / SARscape',
        shortLabel: 'ENVI',
        tone: 'envi',
    },
    envi: {
        label: 'Legacy ENVI',
        shortLabel: 'ENVI-L',
        tone: 'envi',
    },
    isce2: {
        label: 'ISCE2',
        shortLabel: 'ISCE2',
        tone: 'isce2',
    },
    pyint: {
        label: 'PyINT / Gamma',
        shortLabel: 'PyINT',
        tone: 'pyint',
    },
    landsar: {
        label: 'LandSAR',
        shortLabel: 'LandSAR',
        tone: 'landsar',
    },
};

function normalizeEngineCode(value) {
    return String(value || '').trim().toLowerCase();
}

export function getDinsarEngineMeta(engineCode) {
    const normalizedCode = normalizeEngineCode(engineCode);
    const matched = DINSAR_ENGINE_META[normalizedCode];
    if (matched) {
        return {
            code: normalizedCode,
            ...matched,
        };
    }
    return {
        code: normalizedCode || 'unknown',
        label: normalizedCode || 'Unknown',
        shortLabel: normalizedCode || 'Unknown',
        tone: 'unknown',
    };
}

export function buildDinsarEngineOptions(results = [], { includeKnown = false } = {}) {
    const codes = new Set(includeKnown ? KNOWN_DINSAR_ENGINE_CODES : []);

    (Array.isArray(results) ? results : []).forEach((result) => {
        const normalizedCode = normalizeEngineCode(result?.engine_code);
        if (normalizedCode) {
            codes.add(normalizedCode);
        }
    });

    return Array.from(codes)
        .sort((left, right) => {
            const leftIndex = KNOWN_DINSAR_ENGINE_CODES.indexOf(left);
            const rightIndex = KNOWN_DINSAR_ENGINE_CODES.indexOf(right);
            if (leftIndex >= 0 && rightIndex >= 0) {
                return leftIndex - rightIndex;
            }
            if (leftIndex >= 0) {
                return -1;
            }
            if (rightIndex >= 0) {
                return 1;
            }
            return left.localeCompare(right);
        })
        .map((code) => ({
            value: code,
            ...getDinsarEngineMeta(code),
        }));
}
