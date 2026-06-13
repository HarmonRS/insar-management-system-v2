/**
 * 地图导出工具函数
 * 纯函数，无 React 依赖
 */

/* ---------- D-InSAR 伪彩色色表 (from qgis_color.txt) ---------- */

const DINSAR_COLORMAP = [
    { value: -0.10, r: 255, g: 20,  b: 0   },
    { value: -0.08, r: 255, g: 20,  b: 0   },
    { value: -0.06, r: 240, g: 137, b: 0   },
    { value: -0.02, r: 232, g: 240, b: 70  },
    { value:  0.02, r: 24,  g: 255, b: 0   },
    { value:  0.06, r: 101, g: 185, b: 241 },
    { value:  0.08, r: 0,   g: 104, b: 200 },
    { value:  0.10, r: 0,   g: 104, b: 200 },
];

/* ---------- 图层检测 ---------- */

const LAYER_DEFS = [
    {
        id: 'radar_orbit',
        ref: 'activeLayersRef',
        detect: (layers) => {
            const vals = layers ? Object.values(layers) : [];
            const hasGreen = vals.some(l => l?.options?.color === '#48bb78');
            return hasGreen;
        },
        color: '#48bb78', type: 'polygon',
        label: { en: 'Radar Coverage (with orbit)', zh: '雷达覆盖(有精轨)' },
    },
    {
        id: 'radar_no_orbit',
        ref: 'activeLayersRef',
        detect: (layers) => {
            const vals = layers ? Object.values(layers) : [];
            return vals.some(l => l?.options?.color === '#f56565');
        },
        color: '#f56565', type: 'polygon',
        label: { en: 'Radar Coverage (no orbit)', zh: '雷达覆盖(无精轨)' },
    },
    {
        id: 'hazard',
        ref: 'hazardLayersGroupRef',
        detect: (group) => group && typeof group.getLayers === 'function' && group.getLayers().length > 0,
        color: '#e53e3e', type: 'circle',
        label: { en: 'Hazard Points', zh: '隐患点' },
    },
    {
        id: 'dinsar',
        ref: 'dinsarResultLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#ff6b35', type: 'colorbar',
        label: { en: 'D-InSAR Displacement (m)', zh: 'D-InSAR 形变量 (m)' },
    },
    {
        id: 'sbas_rate',
        ref: 'sbasAnalysisLayersRef',
        detect: (layers) => layers && Object.values(layers).some(item => item?.kind === 'rate'),
        color: '#1d4ed8', type: 'colorbar',
        label: { en: 'SBAS LOS Velocity (mm/yr)', zh: 'SBAS LOS 速率 (mm/yr)' },
    },
    {
        id: 'sbas_overview',
        ref: 'sbasAnalysisLayersRef',
        detect: (layers) => layers && Object.values(layers).some(item => item?.kind === 'overview'),
        color: '#7c3aed', type: 'polygon',
        label: { en: 'SBAS Product Footprints', zh: 'SBAS 产品范围' },
    },
    {
        id: 'sbas_points',
        ref: 'sbasAnalysisLayersRef',
        detect: (layers) => layers && Object.values(layers).some(item => item?.kind === 'points' || item?.kind === 'query'),
        color: '#16a34a', type: 'circle',
        label: { en: 'SBAS Monitoring Points', zh: 'SBAS 监测点' },
    },
    {
        id: 'water_scene',
        ref: 'waterSceneLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#00e5cc', type: 'polygon', dash: true,
        label: { en: 'Water Scenes', zh: '水体场景' },
    },
    {
        id: 'radar_preview',
        ref: 'radarPreviewLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#6366f1', type: 'image',
        label: { en: 'Radar Preview', zh: '雷达预览' },
    },
    {
        id: 'pair_master',
        ref: 'pairLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#3498db', type: 'polygon',
        label: { en: 'Pair Master Image', zh: '配对主影像' },
    },
    {
        id: 'pair_slave',
        ref: 'pairLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#2ecc71', type: 'polygon',
        label: { en: 'Pair Slave Image', zh: '配对辅影像' },
    },
    {
        id: 'aoi',
        ref: 'aoeLayerRef',
        detect: (layer) => layer !== null && layer !== undefined,
        color: '#f6e05e', type: 'polygon', dash: true,
        label: { en: 'AOI Boundary', zh: 'AOI 边界' },
    },
    {
        id: 'flood_event',
        ref: 'floodEventLayersRef',
        detect: (layers) => layers && Object.keys(layers).length > 0,
        color: '#e74c3c', type: 'image',
        label: { en: 'Flood Events', zh: '洪涝事件' },
    },
];

export function detectVisibleLayers(layerRefs, language = 'zh') {
    const en = language === 'en';
    const items = [];
    for (const def of LAYER_DEFS) {
        const refVal = layerRefs[def.ref];
        if (def.detect(refVal)) {
            items.push({
                id: def.id,
                type: def.type,
                color: def.color,
                dash: !!def.dash,
                label: en ? def.label.en : def.label.zh,
            });
        }
    }
    return items;
}

/* ---------- 比例尺计算 ---------- */

const NICE_NUMBERS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];

export function calculateScaleBar(map, pixelWidth = 150) {
    if (!map) return { distanceM: 0, label: '', barWidthPx: 0 };
    const size = map.getSize();
    const left = map.containerPointToLatLng([size.x / 2 - pixelWidth / 2, size.y / 2]);
    const right = map.containerPointToLatLng([size.x / 2 + pixelWidth / 2, size.y / 2]);
    const realDist = left.distanceTo(right); // meters
    // find nearest nice number
    let best = NICE_NUMBERS[0];
    for (const n of NICE_NUMBERS) {
        if (n <= realDist) best = n;
        else break;
    }
    const barWidthPx = Math.round(pixelWidth * (best / realDist));
    const label = best >= 1000 ? `${best / 1000} km` : `${best} m`;
    return { distanceM: best, label, barWidthPx };
}

/* ---------- 指北针 SVG ---------- */

export function northArrowSvg(size = 48) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 48 48">
  <polygon points="24,2 30,22 24,18 18,22" fill="#333" stroke="#333" stroke-width="0.5"/>
  <polygon points="24,18 30,22 24,46 18,22" fill="#ccc" stroke="#333" stroke-width="0.5"/>
  <text x="24" y="14" text-anchor="middle" font-size="11" font-weight="bold" fill="#fff" font-family="Arial">N</text>
</svg>`;
}

/* ---------- Canvas 合成 ---------- */

function drawLegendSymbol(ctx, type, color, dash, x, y, size) {
    ctx.save();
    if (type === 'circle') {
        ctx.beginPath();
        ctx.arc(x + size / 2, y + size / 2, size / 2 - 1, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.stroke();
    } else if (type === 'image') {
        ctx.fillStyle = color + '44';
        ctx.fillRect(x, y, size, size);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x, y, size, size);
        ctx.beginPath();
        ctx.moveTo(x, y + size); ctx.lineTo(x + size, y);
        ctx.moveTo(x + size * 0.5, y + size); ctx.lineTo(x + size, y + size * 0.5);
        ctx.moveTo(x, y + size * 0.5); ctx.lineTo(x + size * 0.5, y);
        ctx.stroke();
    } else {
        ctx.fillStyle = color + '33';
        ctx.fillRect(x, y, size, size);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        if (dash) ctx.setLineDash([4, 3]);
        ctx.strokeRect(x, y, size, size);
    }
    ctx.restore();
}

/** 绘制 D-InSAR 渐变色条 + 刻度 */
function drawColorbar(ctx, x, y, barW, barH, S) {
    const cmap = DINSAR_COLORMAP;
    const minVal = cmap[0].value;
    const maxVal = cmap[cmap.length - 1].value;
    const range = maxVal - minVal;

    // draw gradient bar (horizontal)
    for (let px = 0; px < barW; px++) {
        const t = px / barW;
        const val = minVal + t * range;
        // find surrounding stops
        let lo = cmap[0], hi = cmap[cmap.length - 1];
        for (let i = 0; i < cmap.length - 1; i++) {
            if (val >= cmap[i].value && val <= cmap[i + 1].value) {
                lo = cmap[i]; hi = cmap[i + 1]; break;
            }
        }
        const segRange = hi.value - lo.value;
        const f = segRange === 0 ? 0 : (val - lo.value) / segRange;
        const r = Math.round(lo.r + (hi.r - lo.r) * f);
        const g = Math.round(lo.g + (hi.g - lo.g) * f);
        const b = Math.round(lo.b + (hi.b - lo.b) * f);
        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.fillRect(x + px, y, 1, barH);
    }

    // border
    ctx.strokeStyle = '#666';
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, barW, barH);

    // tick labels
    const ticks = [-0.10, -0.06, -0.02, 0.02, 0.06, 0.10];
    ctx.fillStyle = '#0f172a';
    ctx.font = `${9 * S}px "Bahnschrift", sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const tv of ticks) {
        const tx = x + ((tv - minVal) / range) * barW;
        // tick mark
        ctx.beginPath();
        ctx.moveTo(tx, y + barH);
        ctx.lineTo(tx, y + barH + 3 * S);
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillText(tv.toFixed(2), tx, y + barH + 4 * S);
    }
}

export function compositeExportCanvas({
    mapCanvas, title, legendItems, showLegend, showScaleBar,
    showNorthArrow, scaleBarInfo, orgName, logoImg,
}) {
    const S = 2;
    const margin = 32 * S;          // 四周白边
    const mapW = mapCanvas.width;
    const mapH = mapCanvas.height;
    const titleH = title ? 72 * S : 0;
    const footerH = (orgName || logoImg) ? 48 * S : 0;
    const totalW = mapW + margin * 2;
    const totalH = titleH + mapH + footerH + margin * 2;

    const canvas = document.createElement('canvas');
    canvas.width = totalW;
    canvas.height = totalH;
    const ctx = canvas.getContext('2d');

    // white background (always, for clean border)
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, totalW, totalH);

    // outer border
    ctx.strokeStyle = '#c3cfdf';
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, totalW - 2, totalH - 2);

    const mapX = margin;
    const mapY = margin + titleH;

    // title
    if (title) {
        ctx.fillStyle = '#0f172a';
        ctx.font = `bold ${24 * S}px "Bahnschrift", "Segoe UI", sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(title, totalW / 2, margin + titleH / 2);
        // separator line
        ctx.strokeStyle = '#d5dfeb';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(margin, mapY - 4 * S);
        ctx.lineTo(totalW - margin, mapY - 4 * S);
        ctx.stroke();
    }

    // map content
    ctx.drawImage(mapCanvas, mapX, mapY);
    // map border
    ctx.strokeStyle = '#c3cfdf';
    ctx.lineWidth = 1;
    ctx.strokeRect(mapX, mapY, mapW, mapH);

    // north arrow (bigger: 72px base)
    if (showNorthArrow) {
        const arrowSize = 72 * S;
        const pad = 20 * S;
        const ax = mapX + mapW - arrowSize - pad;
        const ay = mapY + pad;
        // white circle background
        ctx.save();
        ctx.beginPath();
        ctx.arc(ax + arrowSize / 2, ay + arrowSize / 2, arrowSize / 2 + 4 * S, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.fill();
        ctx.strokeStyle = '#c3cfdf';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.restore();
        const tmpCanvas = document.createElement('canvas');
        tmpCanvas.width = arrowSize;
        tmpCanvas.height = arrowSize;
        const tmpCtx = tmpCanvas.getContext('2d');
        const s = arrowSize / 48;
        tmpCtx.scale(s, s);
        tmpCtx.fillStyle = '#1a1a1a';
        tmpCtx.beginPath();
        tmpCtx.moveTo(24, 2); tmpCtx.lineTo(30, 22); tmpCtx.lineTo(24, 18); tmpCtx.lineTo(18, 22);
        tmpCtx.closePath(); tmpCtx.fill();
        tmpCtx.fillStyle = '#bbb';
        tmpCtx.strokeStyle = '#1a1a1a';
        tmpCtx.lineWidth = 0.5;
        tmpCtx.beginPath();
        tmpCtx.moveTo(24, 18); tmpCtx.lineTo(30, 22); tmpCtx.lineTo(24, 46); tmpCtx.lineTo(18, 22);
        tmpCtx.closePath(); tmpCtx.fill(); tmpCtx.stroke();
        tmpCtx.fillStyle = '#fff';
        tmpCtx.font = 'bold 12px Arial';
        tmpCtx.textAlign = 'center';
        tmpCtx.textBaseline = 'middle';
        tmpCtx.fillText('N', 24, 11);
        ctx.drawImage(tmpCanvas, ax, ay);
    }

    // scale bar (bigger: 200px base width, thicker)
    if (showScaleBar && scaleBarInfo && scaleBarInfo.barWidthPx > 0) {
        const barW = scaleBarInfo.barWidthPx * S * 1.4;
        const barH = 8 * S;
        const pad = 20 * S;
        const bx = mapX + mapW - barW - pad;
        const by = mapY + mapH - pad - barH - 24 * S;
        // background
        ctx.fillStyle = 'rgba(255,255,255,0.88)';
        ctx.beginPath();
        ctx.roundRect(bx - 8 * S, by - 22 * S, barW + 16 * S, barH + 36 * S, 4 * S);
        ctx.fill();
        // label
        ctx.fillStyle = '#1a1a1a';
        ctx.font = `bold ${14 * S}px "Bahnschrift", sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(scaleBarInfo.label, bx + barW / 2, by - 4 * S);
        // bar (alternating black/white segments)
        ctx.fillStyle = '#1a1a1a';
        ctx.fillRect(bx, by, barW, barH);
        ctx.fillStyle = '#fff';
        ctx.fillRect(bx + barW * 0.25, by, barW * 0.25, barH);
        ctx.fillRect(bx + barW * 0.75, by, barW * 0.25, barH);
        ctx.strokeStyle = '#1a1a1a';
        ctx.lineWidth = 1;
        ctx.strokeRect(bx, by, barW, barH);
        // end ticks
        const tickH = 5 * S;
        ctx.beginPath();
        ctx.moveTo(bx, by - tickH); ctx.lineTo(bx, by + barH + tickH);
        ctx.moveTo(bx + barW, by - tickH); ctx.lineTo(bx + barW, by + barH + tickH);
        ctx.moveTo(bx + barW / 2, by - tickH); ctx.lineTo(bx + barW / 2, by + barH + tickH);
        ctx.stroke();
    }

    // legend (bigger fonts and symbols)
    if (showLegend && legendItems && legendItems.length > 0) {
        const fontSize = 15 * S;
        const symSize = 18 * S;
        const lineH = 28 * S;
        const padX = 16 * S;
        const padY = 14 * S;
        const titleFontSize = 17 * S;
        const headerH = titleFontSize + padY;
        const colorbarW = 200 * S;
        const colorbarH = 16 * S;
        const colorbarExtraH = colorbarH + 26 * S;

        let contentH = 0;
        for (const item of legendItems) {
            contentH += item.type === 'colorbar' ? (lineH + colorbarExtraH) : lineH;
        }
        const legendH = headerH + padY + contentH + padY;

        ctx.font = `${fontSize}px "Bahnschrift", sans-serif`;
        let maxTextW = 0;
        for (const item of legendItems) {
            maxTextW = Math.max(maxTextW, ctx.measureText(item.label).width);
        }
        const legendW = Math.max(padX + symSize + 10 * S + maxTextW + padX, padX + colorbarW + padX);
        const lx = mapX + 20 * S;
        const ly = mapY + mapH - legendH - 20 * S;
        ctx.fillStyle = 'rgba(255,255,255,0.92)';
        ctx.strokeStyle = '#c3cfdf';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(lx, ly, legendW, legendH, 8 * S);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#0f172a';
        ctx.font = `bold ${titleFontSize}px "Bahnschrift", sans-serif`;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText('Legend', lx + padX, ly + padY + titleFontSize / 2);
        ctx.font = `${fontSize}px "Bahnschrift", sans-serif`;
        let curY = ly + headerH + padY;
        legendItems.forEach((item) => {
            if (item.type === 'colorbar') {
                ctx.fillStyle = '#0f172a';
                ctx.textBaseline = 'middle';
                ctx.fillText(item.label, lx + padX, curY + lineH / 2);
                curY += lineH;
                drawColorbar(ctx, lx + padX, curY, colorbarW, colorbarH, S);
                curY += colorbarExtraH;
            } else {
                drawLegendSymbol(ctx, item.type, item.color, item.dash, lx + padX, curY + (lineH - symSize) / 2, symSize);
                ctx.fillStyle = '#0f172a';
                ctx.textBaseline = 'middle';
                ctx.fillText(item.label, lx + padX + symSize + 10 * S, curY + lineH / 2);
                curY += lineH;
            }
        });
    }

    // footer: org name + logo
    if (orgName || logoImg) {
        const fy = mapY + mapH + 8 * S;
        // separator
        ctx.strokeStyle = '#d5dfeb';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(margin, fy);
        ctx.lineTo(totalW - margin, fy);
        ctx.stroke();

        const textY = fy + footerH / 2 - 2 * S;

        // logo on the left
        if (logoImg) {
            const logoH = 32 * S;
            const logoW = Math.round(logoH * (logoImg.naturalWidth / logoImg.naturalHeight));
            const logoX = margin + 4 * S;
            const logoY = textY - logoH / 2;
            ctx.drawImage(logoImg, logoX, logoY, logoW, logoH);
            // org name after logo
            if (orgName) {
                ctx.fillStyle = '#4b5a70';
                ctx.font = `${14 * S}px "Bahnschrift", "Segoe UI", sans-serif`;
                ctx.textAlign = 'left';
                ctx.textBaseline = 'middle';
                ctx.fillText(orgName, logoX + logoW + 10 * S, textY);
            }
        } else if (orgName) {
            ctx.fillStyle = '#4b5a70';
            ctx.font = `${14 * S}px "Bahnschrift", "Segoe UI", sans-serif`;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(orgName, margin + 4 * S, textY);
        }

        // date on the right
        const dateStr = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
        ctx.fillStyle = '#6b7a90';
        ctx.font = `${12 * S}px "Bahnschrift", sans-serif`;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(dateStr, totalW - margin - 4 * S, textY);
    }

    return canvas;
}

/* ---------- Blob / 下载 ---------- */

export function canvasToBlob(canvas, format = 'png', quality = 0.92) {
    return new Promise((resolve) => {
        const mime = format === 'jpeg' ? 'image/jpeg' : 'image/png';
        canvas.toBlob((blob) => resolve(blob), mime, quality);
    });
}

export function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/* ---------- 预览缩略图 ---------- */

export function generatePreviewDataUrl(canvas, maxWidth = 480) {
    if (!canvas) return '';
    const ratio = canvas.height / canvas.width;
    const w = Math.min(maxWidth, canvas.width);
    const h = Math.round(w * ratio);
    const tmp = document.createElement('canvas');
    tmp.width = w;
    tmp.height = h;
    const ctx = tmp.getContext('2d');
    ctx.drawImage(canvas, 0, 0, w, h);
    return tmp.toDataURL('image/png');
}
