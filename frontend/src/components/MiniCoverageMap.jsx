import { useEffect, useMemo, useRef } from 'react';
import L from 'leaflet';
import { getBaseLayerConfig, TILE_LAYER_DEFAULT_KEY, TILE_LAYER_OPTIONS } from '../config/appConstants';

const DEFAULT_HEIGHT = 260;

const palette = ['#2563eb', '#16a34a', '#dc2626', '#7c3aed', '#0891b2', '#d97706'];

function normalizeBbox(bbox) {
  if (!bbox || typeof bbox !== 'object') return null;
  const minLon = Number(bbox.min_lon);
  const minLat = Number(bbox.min_lat);
  const maxLon = Number(bbox.max_lon);
  const maxLat = Number(bbox.max_lat);
  if (![minLon, minLat, maxLon, maxLat].every(Number.isFinite)) return null;
  if (minLon >= maxLon || minLat >= maxLat) return null;
  return { min_lon: minLon, min_lat: minLat, max_lon: maxLon, max_lat: maxLat };
}

function normalizePolygon(points) {
  if (!Array.isArray(points) || points.length < 3) return null;
  const latLngs = points
    .filter(point => Array.isArray(point) && point.length >= 2)
    .map(point => [Number(point[1]), Number(point[0])])
    .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
  return latLngs.length >= 3 ? latLngs : null;
}

function normalizeFeatureCollection(value) {
  if (!value || typeof value !== 'object') {
    return { type: 'FeatureCollection', features: [] };
  }
  if (value.type === 'FeatureCollection' && Array.isArray(value.features)) {
    return value;
  }
  if (value.type === 'Feature') {
    return { type: 'FeatureCollection', features: [value] };
  }
  if (value.type && value.coordinates) {
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry: value }],
    };
  }
  return { type: 'FeatureCollection', features: [] };
}

function bboxToBounds(bbox) {
  const normalized = normalizeBbox(bbox);
  if (!normalized) return null;
  return L.latLngBounds(
    [normalized.min_lat, normalized.min_lon],
    [normalized.max_lat, normalized.max_lon],
  );
}

function featureLabel(feature) {
  const props = feature?.properties || {};
  return props.label || props.scene_name || props.date || props.imaging_date || props.name || '';
}

export default function MiniCoverageMap({
  title = '范围预览',
  subtitle = '',
  polygons = [],
  bboxes = [],
  geojson,
  height = DEFAULT_HEIGHT,
  emptyText = '暂无可绘制范围',
}) {
  const mapElementRef = useRef(null);
  const mapRef = useRef(null);
  const layerGroupRef = useRef(null);
  const tileLayerRef = useRef(null);

  const safePolygons = useMemo(() => (
    (polygons || [])
      .map((item, index) => ({
        ...item,
        latLngs: normalizePolygon(item?.points || item?.polygon || item?.coverage_polygon),
        color: item?.color || palette[index % palette.length],
      }))
      .filter(item => item.latLngs)
  ), [polygons]);

  const safeBboxes = useMemo(() => (
    (bboxes || [])
      .map((item, index) => ({
        ...item,
        bounds: bboxToBounds(item?.bbox || item),
        color: item?.color || palette[(index + safePolygons.length) % palette.length],
      }))
      .filter(item => item.bounds?.isValid?.())
  ), [bboxes, safePolygons.length]);

  const safeGeojson = useMemo(() => normalizeFeatureCollection(geojson), [geojson]);
  const hasDrawable = safePolygons.length > 0 || safeBboxes.length > 0 || safeGeojson.features.length > 0;

  useEffect(() => {
    if (hasDrawable || !mapRef.current) return;
    mapRef.current.remove();
    mapRef.current = null;
    layerGroupRef.current = null;
    tileLayerRef.current = null;
  }, [hasDrawable]);

  useEffect(() => {
    if (!mapElementRef.current || !hasDrawable) return undefined;
    if (!mapRef.current) {
      mapRef.current = L.map(mapElementRef.current, {
        attributionControl: false,
        zoomControl: true,
        scrollWheelZoom: false,
        doubleClickZoom: false,
        boxZoom: false,
        keyboard: false,
        dragging: true,
      });
      const baseLayer = getBaseLayerConfig(TILE_LAYER_DEFAULT_KEY);
      tileLayerRef.current = L.tileLayer(baseLayer.url, {
        ...TILE_LAYER_OPTIONS,
        attribution: baseLayer.attribution,
      }).addTo(mapRef.current);
      layerGroupRef.current = L.layerGroup().addTo(mapRef.current);
    }

    const map = mapRef.current;
    const layerGroup = layerGroupRef.current;
    layerGroup.clearLayers();
    let fitBounds = null;

    safeBboxes.forEach((item) => {
      L.rectangle(item.bounds, {
        color: item.color,
        weight: item.weight || 2,
        dashArray: item.dashArray || '5 5',
        fillColor: item.fillColor || item.color,
        fillOpacity: item.fillOpacity ?? 0.05,
      })
        .bindTooltip(item.label || 'bbox', { sticky: true })
        .addTo(layerGroup);
      fitBounds = fitBounds ? fitBounds.extend(item.bounds) : item.bounds;
    });

    safePolygons.forEach((item) => {
      const layer = L.polygon(item.latLngs, {
        color: item.color,
        weight: item.weight || 2,
        opacity: 0.9,
        fillColor: item.fillColor || item.color,
        fillOpacity: item.fillOpacity ?? 0.12,
      })
        .bindTooltip(item.label || 'footprint', { sticky: true })
        .addTo(layerGroup);
      const bounds = layer.getBounds();
      if (bounds.isValid()) {
        fitBounds = fitBounds ? fitBounds.extend(bounds) : bounds;
      }
    });

    if (safeGeojson.features.length > 0) {
      const geoLayer = L.geoJSON(safeGeojson, {
        style: feature => ({
          color: feature?.properties?.color || '#0f766e',
          weight: 1.8,
          opacity: 0.95,
          fillColor: feature?.properties?.fillColor || feature?.properties?.color || '#14b8a6',
          fillOpacity: 0.1,
        }),
        onEachFeature: (feature, layer) => {
          const label = featureLabel(feature);
          if (label) layer.bindTooltip(label, { sticky: true });
        },
      }).addTo(layerGroup);
      const bounds = geoLayer.getBounds();
      if (bounds.isValid()) {
        fitBounds = fitBounds ? fitBounds.extend(bounds) : bounds;
      }
    }

    if (fitBounds?.isValid?.()) {
      map.fitBounds(fitBounds.pad(0.12), { animate: false, maxZoom: 12 });
    }
    window.setTimeout(() => map.invalidateSize(), 0);
    return undefined;
  }, [hasDrawable, safeBboxes, safeGeojson, safePolygons]);

  useEffect(() => () => {
    if (mapRef.current) {
      mapRef.current.remove();
      mapRef.current = null;
      layerGroupRef.current = null;
      tileLayerRef.current = null;
    }
  }, []);

  return (
    <section
      style={{
        border: '1px solid #d8dee8',
        borderRadius: 8,
        overflow: 'hidden',
        background: '#ffffff',
      }}
    >
      <div style={{ padding: '10px 12px', borderBottom: '1px solid #e2e8f0', display: 'flex', justifyContent: 'space-between', gap: 10 }}>
        <strong style={{ color: '#0f172a', fontSize: 13 }}>{title}</strong>
        {subtitle && <span style={{ color: '#64748b', fontSize: 12 }}>{subtitle}</span>}
      </div>
      {hasDrawable ? (
        <div ref={mapElementRef} style={{ height, minHeight: 180 }} />
      ) : (
        <div style={{ height, minHeight: 180, display: 'grid', placeItems: 'center', color: '#64748b', fontSize: 13, background: '#f8fafc' }}>
          {emptyText}
        </div>
      )}
    </section>
  );
}
