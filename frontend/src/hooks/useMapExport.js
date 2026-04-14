import { useState, useRef, useCallback } from 'react';
import {
    detectVisibleLayers,
    calculateScaleBar,
    compositeExportCanvas,
    canvasToBlob,
    downloadBlob,
    generatePreviewDataUrl,
} from '../utils/mapExportHelpers';

let html2canvasLoader = null;

const MAP_EXPORT_RESOLUTIONS = {
    '1920x1080': { width: 1920, height: 1080, label: '1920x1080 (Full HD)' },
    '2560x1440': { width: 2560, height: 1440, label: '2560x1440 (2K)' },
    '3840x2160': { width: 3840, height: 2160, label: '3840x2160 (4K)' },
};

async function loadHtml2Canvas() {
    if (!html2canvasLoader) {
        html2canvasLoader = import('html2canvas').then((module) => module.default || module);
    }
    return html2canvasLoader;
}

export default function useMapExport({ mapRef, getVisibleLayerRefs, addLog, language }) {
    const [showExportModal, setShowExportModal] = useState(false);
    const [exportTitle, setExportTitle] = useState('');
    const [exportFormat, setExportFormat] = useState('png');
    const [exportResolution, setExportResolution] = useState('1920x1080');
    const [showLegend, setShowLegend] = useState(true);
    const [showScaleBar, setShowScaleBar] = useState(true);
    const [showNorthArrow, setShowNorthArrow] = useState(true);
    const [legendItems, setLegendItems] = useState([]);
    const [previewUrl, setPreviewUrl] = useState('');
    const [isCapturing, setIsCapturing] = useState(false);
    const [isExporting, setIsExporting] = useState(false);
    const [exportOrg, setExportOrg] = useState(import.meta.env.VITE_MAP_EXPORT_ORG || '');
    const [logoDataUrl, setLogoDataUrl] = useState('');

    const mapCanvasRef = useRef(null);
    const scaleBarInfoRef = useRef(null);
    const logoImgRef = useRef(null);
    const originalMapSizeRef = useRef(null);
    const en = language === 'en';

    const loadLogoImg = useCallback((dataUrl) => {
        return new Promise((resolve) => {
            if (!dataUrl) {
                logoImgRef.current = null;
                resolve(null);
                return;
            }
            const img = new Image();
            img.onload = () => {
                logoImgRef.current = img;
                resolve(img);
            };
            img.onerror = () => {
                logoImgRef.current = null;
                resolve(null);
            };
            img.src = dataUrl;
        });
    }, []);

    const handleLogoUpload = useCallback((e) => {
        const file = e.target.files?.[0];
        if (!file || !file.type.startsWith('image/')) {
            return;
        }
        const reader = new FileReader();
        reader.onload = async (ev) => {
            const dataUrl = ev.target.result;
            setLogoDataUrl(dataUrl);
            await loadLogoImg(dataUrl);
        };
        reader.readAsDataURL(file);
    }, [loadLogoImg]);

    const removeLogo = useCallback(() => {
        setLogoDataUrl('');
        logoImgRef.current = null;
    }, []);

    const buildComposite = useCallback((opts = {}) => {
        if (!mapCanvasRef.current) {
            return null;
        }
        return compositeExportCanvas({
            mapCanvas: mapCanvasRef.current,
            title: opts.title ?? exportTitle,
            legendItems: opts.legendItems ?? legendItems,
            showLegend: opts.showLegend ?? showLegend,
            showScaleBar: opts.showScaleBar ?? showScaleBar,
            showNorthArrow: opts.showNorthArrow ?? showNorthArrow,
            scaleBarInfo: scaleBarInfoRef.current,
            format: opts.format ?? exportFormat,
            orgName: opts.orgName ?? exportOrg,
            logoImg: logoImgRef.current,
        });
    }, [exportTitle, legendItems, showLegend, showScaleBar, showNorthArrow, exportFormat, exportOrg]);

    const refreshPreview = useCallback((opts = {}) => {
        const canvas = buildComposite(opts);
        if (canvas) {
            setPreviewUrl(generatePreviewDataUrl(canvas, 480));
        }
    }, [buildComposite]);

    const captureMapCanvas = useCallback(async (resolutionKey, detectedLegendItems = legendItems) => {
        const map = mapRef.current;
        const mapEl = document.getElementById('map');
        if (!map || !mapEl) {
            throw new Error('Map element not found');
        }

        const resolution = MAP_EXPORT_RESOLUTIONS[resolutionKey] || MAP_EXPORT_RESOLUTIONS['1920x1080'];

        if (!originalMapSizeRef.current) {
            originalMapSizeRef.current = {
                width: mapEl.style.width,
                height: mapEl.style.height,
            };
        }

        mapEl.style.width = `${resolution.width}px`;
        mapEl.style.height = `${resolution.height}px`;
        map.invalidateSize();

        try {
            await new Promise((resolve) => setTimeout(resolve, 100));

            const scaleBarInfo = calculateScaleBar(map);
            scaleBarInfoRef.current = scaleBarInfo;

            const html2canvas = await loadHtml2Canvas();
            const canvas = await html2canvas(mapEl, {
                useCORS: true,
                allowTaint: true,
                scale: 2,
                logging: false,
                backgroundColor: null,
                ignoreElements: (el) => el.classList?.contains('leaflet-control-container'),
            });
            mapCanvasRef.current = canvas;

            return {
                canvas,
                scaleBarInfo,
                legendItems: detectedLegendItems,
            };
        } finally {
            mapEl.style.width = originalMapSizeRef.current.width;
            mapEl.style.height = originalMapSizeRef.current.height;
            map.invalidateSize();
        }
    }, [legendItems, mapRef]);

    const openExportModal = useCallback(async () => {
        setShowExportModal(true);
        setExportTitle('');
        setExportFormat('png');
        setExportResolution('1920x1080');
        setShowLegend(true);
        setShowScaleBar(true);
        setShowNorthArrow(true);
        setPreviewUrl('');
        setIsCapturing(true);
        setExportOrg(import.meta.env.VITE_MAP_EXPORT_ORG || '');

        try {
            const detected = detectVisibleLayers(getVisibleLayerRefs(), language);
            setLegendItems(detected);

            const { canvas, scaleBarInfo } = await captureMapCanvas('1920x1080', detected);
            const composite = compositeExportCanvas({
                mapCanvas: canvas,
                title: '',
                legendItems: detected,
                showLegend: true,
                showScaleBar: true,
                showNorthArrow: true,
                scaleBarInfo,
                format: 'png',
                orgName: import.meta.env.VITE_MAP_EXPORT_ORG || '',
                logoImg: logoImgRef.current,
            });
            setPreviewUrl(generatePreviewDataUrl(composite, 480));
        } catch (err) {
            addLog('error', en ? `Map capture failed: ${err.message}` : `地图截图失败: ${err.message}`);
            setShowExportModal(false);
        } finally {
            setIsCapturing(false);
        }
    }, [addLog, captureMapCanvas, en, getVisibleLayerRefs, language]);

    const closeExportModal = useCallback(() => {
        setShowExportModal(false);
        mapCanvasRef.current = null;
        setPreviewUrl('');
    }, []);

    const recaptureWithResolution = useCallback(async (resolutionKey) => {
        setIsCapturing(true);
        try {
            await captureMapCanvas(resolutionKey);
            refreshPreview();
        } catch (err) {
            addLog('error', en ? `Recapture failed: ${err.message}` : `重新截图失败: ${err.message}`);
        } finally {
            setIsCapturing(false);
        }
    }, [addLog, captureMapCanvas, en, refreshPreview]);

    const handleResolutionChange = useCallback((resolutionKey) => {
        setExportResolution(resolutionKey);
        void recaptureWithResolution(resolutionKey);
    }, [recaptureWithResolution]);

    const executeExport = useCallback(async () => {
        setIsExporting(true);
        try {
            const canvas = buildComposite();
            if (!canvas) {
                throw new Error('No canvas');
            }
            const blob = await canvasToBlob(canvas, exportFormat);
            const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            const filename = `map_export_${ts}.${exportFormat === 'jpeg' ? 'jpg' : 'png'}`;
            downloadBlob(blob, filename);
            addLog('info', en ? `Map exported: ${filename}` : `地图已导出: ${filename}`);
            closeExportModal();
        } catch (err) {
            addLog('error', en ? `Export failed: ${err.message}` : `导出失败: ${err.message}`);
        } finally {
            setIsExporting(false);
        }
    }, [addLog, buildComposite, closeExportModal, en, exportFormat]);

    const updateLegendItem = useCallback((id, changes) => {
        setLegendItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...changes } : item)));
    }, []);

    const removeLegendItem = useCallback((id) => {
        setLegendItems((prev) => prev.filter((item) => item.id !== id));
    }, []);

    const addLegendItem = useCallback(() => {
        setLegendItems((prev) => ([
            ...prev,
            {
                id: `custom_${Date.now()}`,
                type: 'polygon',
                color: '#888888',
                dash: false,
                label: en ? 'New Item' : '新图例',
            },
        ]));
    }, [en]);

    return {
        showExportModal,
        exportTitle,
        setExportTitle,
        exportFormat,
        setExportFormat,
        exportResolution,
        RESOLUTIONS: MAP_EXPORT_RESOLUTIONS,
        handleResolutionChange,
        showLegend,
        setShowLegend,
        showScaleBar,
        setShowScaleBar,
        showNorthArrow,
        setShowNorthArrow,
        legendItems,
        previewUrl,
        isCapturing,
        isExporting,
        exportOrg,
        setExportOrg,
        logoDataUrl,
        handleLogoUpload,
        removeLogo,
        openExportModal,
        closeExportModal,
        refreshPreview,
        executeExport,
        updateLegendItem,
        removeLegendItem,
        addLegendItem,
    };
}
