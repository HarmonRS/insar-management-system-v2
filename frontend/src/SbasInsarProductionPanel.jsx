import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import MiniCoverageMap from './components/MiniCoverageMap';

import {
  auditSbasInsarStack,
  decideSbasInsarItab,
  deleteSbasInsarRun,
  getLandsarSbasRun,
  getLandsarSbasRunArtifactUrl,
  discoverSbasInsarStacks,
  getSbasInsarCapabilities,
  getSbasInsarRun,
  getSbasInsarRunArtifactUrl,
  listLandsarSbasRuns,
  listSbasInsarRuns,
  prepareSbasInsarCoregistration,
  prepareSbasInsarInterferograms,
  prepareSbasInsarIptaTimeseries,
  prepareSbasInsarRdcDem,
  prepareSbasInsarWorkflow,
  runSbasInsarBaselineAudit,
  submitLandsarSbasAutoWorkflow,
  submitSbasInsarCoregistrationJob,
  submitSbasInsarInterferogramsJob,
  submitSbasInsarIptaTimeseriesJob,
  submitSbasInsarRdcDemJob,
  submitSbasInsarRun,
  submitSbasInsarWorkflowJob,
} from './api/sbasInsarProduction';

const shellStyle = {
  display: 'grid',
  gap: 12,
};

const sectionStyle = {
  background: '#ffffff',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  padding: 14,
};

const mutedStyle = {
  color: '#64748b',
  fontSize: 12,
  lineHeight: 1.55,
};

const ACTIVE_RUN_REFRESH_INTERVAL_MS = 10 * 60 * 1000;

const labelStyle = {
  color: '#475569',
  fontSize: 12,
};

const valueStyle = {
  color: '#0f172a',
  fontSize: 14,
  fontWeight: 650,
};

const metricGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
  gap: 10,
};

const compactDetailsStyle = {
  border: '1px solid #e2e8f0',
  borderRadius: 8,
  padding: '9px 10px',
  background: '#ffffff',
};

const compactSummaryStyle = {
  cursor: 'pointer',
  color: '#0f172a',
  fontSize: 13,
  fontWeight: 700,
};

const buttonBaseStyle = {
  width: '100%',
  textAlign: 'left',
  border: '1px solid #d8dee8',
  borderRadius: 8,
  background: '#ffffff',
  padding: '10px 12px',
  cursor: 'pointer',
};

function formatValue(value, suffix = '') {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return `${numeric.toFixed(Math.abs(numeric) >= 100 ? 1 : 2)}${suffix}`;
}

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

function formatCoord(value, digits = 5) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return numeric.toFixed(digits);
}

function formatBbox(bbox) {
  const normalized = normalizeBbox(bbox);
  if (!normalized) return '-';
  return [
    formatCoord(normalized.min_lon),
    formatCoord(normalized.min_lat),
    formatCoord(normalized.max_lon),
    formatCoord(normalized.max_lat),
  ].join(', ');
}

function bboxCenter(bbox) {
  const normalized = normalizeBbox(bbox);
  if (!normalized) return null;
  return {
    lon: (normalized.min_lon + normalized.max_lon) / 2,
    lat: (normalized.min_lat + normalized.max_lat) / 2,
  };
}

function formatCenter(center) {
  if (!center) return '-';
  const lon = Number(center.lon);
  const lat = Number(center.lat);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return '-';
  return `${formatCoord(lon)}, ${formatCoord(lat)}`;
}

function formatAdminRegion(region) {
  if (!region || typeof region !== 'object') return '-';
  return region.display_name || region.name || region.tree_id || '-';
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '-';
  return `${(numeric * 100).toFixed(numeric >= 0.1 ? 0 : 1)}%`;
}

function shortHash(value) {
  const text = String(value || '').trim();
  return text ? text.slice(0, 10) : '-';
}

function getSceneNames(stack) {
  const sceneNames = Array.isArray(stack?.scene_names) ? stack.scene_names.filter(Boolean) : [];
  if (sceneNames.length > 0) return sceneNames;
  const scenes = Array.isArray(stack?.scenes) ? stack.scenes : [];
  const names = scenes.map(scene => scene?.scene_name).filter(Boolean);
  if (names.length > 0) return names;
  return Array.isArray(stack?.scene_name_preview) ? stack.scene_name_preview.filter(Boolean) : [];
}

function isActiveRunStatus(value) {
  const text = String(value || '').toUpperCase();
  return text.includes('RUNNING') || ['READY', 'PENDING', 'RETRY'].includes(text);
}

function StatusBadge({ value }) {
  const text = String(value || 'UNKNOWN').toUpperCase();
  const okValues = new Set([
    'READY',
    'READY_FOR_GAMMA_BASELINE_AUDIT',
    'WORKFLOW_READY',
    'WORKFLOW_COMPLETED',
    'COMPLETED',
    'IPTA_TIMESERIES_READY',
  ]);
  const isRunning = text.includes('RUNNING');
  const isOk = okValues.has(text);
  const color = isRunning ? '#0369a1' : (isOk ? '#0f766e' : '#92400e');
  const background = isRunning ? '#e0f2fe' : (isOk ? '#ccfbf1' : '#fef3c7');
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 999,
        color,
        background,
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 999, background: color }} />
      {value || 'UNKNOWN'}
    </span>
  );
}

function Metric({ label, value }) {
  return (
    <div
      style={{
        border: '1px solid #e2e8f0',
        borderRadius: 8,
        padding: '9px 10px',
        background: '#f8fafc',
        minHeight: 58,
      }}
    >
      <div style={labelStyle}>{label}</div>
      <div style={{ ...valueStyle, marginTop: 4 }}>{value}</div>
    </div>
  );
}

function StackIdentityNotice({ stack }) {
  if (!stack) return null;
  const sameDateCount = Number(stack.same_date_sequence_candidate_count || 0);
  const distinctSceneCount = Number(stack.same_date_sequence_distinct_scene_group_count || 0);
  const sameSceneRuns = Array.isArray(stack.existing_same_scene_runs) ? stack.existing_same_scene_runs : [];
  const showDateSequenceNotice = sameDateCount > 1 && distinctSceneCount > 1;
  if (!showDateSequenceNotice && sameSceneRuns.length === 0) return null;

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {showDateSequenceNotice && (
        <div style={{ border: '1px solid #fed7aa', borderRadius: 8, padding: 10, background: '#fff7ed', color: '#9a3412', fontSize: 12, lineHeight: 1.55 }}>
          同日期序列候选 {sameDateCount} 个，其中不同影像组 {distinctSceneCount} 个。判断是否同任务请以影像名称集合为准。
        </div>
      )}
      {sameSceneRuns.length > 0 && (
        <div style={{ border: '1px solid #fecaca', borderRadius: 8, padding: 10, background: '#fef2f2', color: '#991b1b', fontSize: 12, lineHeight: 1.55 }}>
          已存在同影像任务：{sameSceneRuns.map(item => `${item.run_id} (${item.status || '-'})`).join('；')}
        </div>
      )}
    </div>
  );
}

function SceneNamePanel({ stack }) {
  if (!stack) return null;
  const names = getSceneNames(stack);
  return (
    <details style={compactDetailsStyle}>
      <summary style={compactSummaryStyle}>
        影像名称 {names.length || stack.scene_name_count || 0} 景；影像组 {shortHash(stack.scene_identity_hash)}
      </summary>
      {names.length > 0 ? (
        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
          {names.map(name => (
            <div key={name} style={{ ...mutedStyle, wordBreak: 'break-all' }}>
              {name}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ ...mutedStyle, marginTop: 8 }}>当前候选未返回完整影像名称；请先执行审计或重新发现候选。</div>
      )}
    </details>
  );
}

function RuntimeStatusPanel({ status }) {
  if (!status) return null;
  const background = status.background_activity || {};
  const tasks = Array.isArray(background.tasks) ? background.tasks : [];
  const jobs = Array.isArray(background.jobs) ? background.jobs : [];
  const taskLogs = Array.isArray(background.task_logs) ? background.task_logs : [];
  const fileLogs = Array.isArray(status.recent_logs) ? status.recent_logs : [];
  const wslProcesses = Array.isArray(status.wsl_processes?.processes) ? status.wsl_processes.processes : [];
  const currentTask = tasks.find(item => ['PENDING', 'RUNNING'].includes(String(item.status || '').toUpperCase())) || tasks[0] || null;
  const currentJob = jobs.find(item => ['READY', 'PENDING', 'RUNNING', 'RETRY'].includes(String(item.status || '').toUpperCase())) || jobs[0] || null;
  const latestTaskLog = taskLogs[0] || null;
  const latestFileLog = fileLogs[0] || null;
  const gate = status.overlap_gate || {};

  return (
    <div style={{ border: '1px solid #bae6fd', borderRadius: 8, padding: 10, background: '#f0f9ff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={valueStyle}>运行状态</div>
        <StatusBadge value={status.active ? 'RUNNING' : (status.run_status || 'IDLE')} />
      </div>
      <div style={{ ...metricGridStyle, marginTop: 8 }}>
        <Metric label="当前步骤" value={status.current_step?.id || '-'} />
        <Metric label="Workflow 更新时间" value={status.workflow_updated_at || '-'} />
        <Metric label="最近日志" value={status.latest_log_updated_at || '-'} />
        <Metric
          label="公共重叠率"
          value={`${formatPercent(gate.common_overlap_ratio)} / ${formatPercent(gate.min_common_overlap_ratio)}`}
        />
      </div>
      {(currentTask || currentJob) && (
        <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-word' }}>
          Task：{currentTask ? `${currentTask.task_type || '-'} ${currentTask.status || '-'} ${currentTask.progress ?? 0}%` : '-'}
          {'; '}
          Job：{currentJob ? `${currentJob.job_type || '-'} ${currentJob.status || '-'}` : '-'}
        </div>
      )}
      {latestTaskLog && (
        <div style={{ ...mutedStyle, marginTop: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          DB 日志：[{latestTaskLog.level || 'INFO'}] {latestTaskLog.message}
        </div>
      )}
      {latestFileLog?.tail && (
        <details style={{ ...compactDetailsStyle, marginTop: 8, borderColor: '#bae6fd' }}>
          <summary style={compactSummaryStyle}>{latestFileLog.name || '最近日志'}</summary>
          <pre
            style={{
              margin: '8px 0 0',
              maxHeight: 180,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 11,
              lineHeight: 1.45,
              color: '#0f172a',
            }}
          >
            {latestFileLog.tail}
          </pre>
        </details>
      )}
      <details style={{ ...compactDetailsStyle, marginTop: 8, borderColor: '#bae6fd' }}>
        <summary style={compactSummaryStyle}>WSL 进程（{wslProcesses.length}）</summary>
        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
          {wslProcesses.length === 0 && (
            <div style={mutedStyle}>{status.wsl_processes?.error || '未发现匹配的 WSL 进程。'}</div>
          )}
          {wslProcesses.map(item => (
            <div key={`${item.pid}-${item.command}`} style={{ ...mutedStyle, fontFamily: 'monospace', wordBreak: 'break-word' }}>
              {item.pid} {item.etime} {item.stat} {item.command}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function RunArtifactLink({ runId, artifact }) {
  const href = getSbasInsarRunArtifactUrl(runId, artifact.relative_path);
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      style={{ color: '#1d4ed8', fontWeight: 650, textDecoration: 'none' }}
    >
      打开
    </a>
  );
}

function LocationSummaryPanel({ coverage }) {
  const bbox = normalizeBbox(coverage?.bbox);
  const intersection = normalizeBbox(coverage?.bbox_intersection);
  const center = coverage?.center || bboxCenter(bbox);
  const adminRegion = coverage?.admin_region;
  const monitorPoints = Array.isArray(coverage?.monitor_points) ? coverage.monitor_points : [];
  return (
    <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
        <div style={valueStyle}>位置摘要</div>
        <span style={mutedStyle}>center / admin region</span>
      </div>
      <div style={{ ...metricGridStyle, marginTop: 8 }}>
        <Metric label="中心点" value={formatCenter(center)} />
        <Metric label="行政区" value={formatAdminRegion(adminRegion)} />
        <Metric label="Stack bbox" value={formatBbox(bbox)} />
        <Metric label="交集 bbox" value={formatBbox(intersection)} />
        <Metric label="单景范围数" value={`${(coverage?.scene_footprints_geojson?.features || []).length || coverage?.scene_bbox_count || 0}`} />
        <Metric label="监测点" value={`${monitorPoints.length}`} />
      </div>
      {monitorPoints.length > 0 && (
        <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-word' }}>
          监测点：{monitorPoints.map(point => `${point.point_id || 'point'} (${formatCenter(point)})`).join('；')}
        </div>
      )}
    </div>
  );
}

function StackCoverageMiniMap({ stack, coverage, title = 'SBAS序列范围预览' }) {
  const source = coverage || stack || {};
  const bbox = source.bbox || source.stack_bbox || stack?.bbox || stack?.bbox_intersection;
  const intersection = source.bbox_intersection || stack?.bbox_intersection;
  const sceneFootprints = source.scene_footprints_geojson || stack?.scene_footprints_geojson;
  const coverageGeojson = source.geojson || stack?.geojson || sceneFootprints;
  const bboxes = [
    bbox && {
      bbox,
      label: 'stack bbox',
      color: '#2563eb',
      fillOpacity: 0.05,
    },
    intersection && {
      bbox: intersection,
      label: 'common overlap',
      color: '#16a34a',
      fillOpacity: 0.12,
      dashArray: null,
    },
  ].filter(Boolean);
  const sceneCount = (sceneFootprints?.features || []).length || source.scene_bbox_count || stack?.usable_scene_count || stack?.scene_count || 0;
  return (
    <MiniCoverageMap
      title={title}
      subtitle={sceneCount ? `${sceneCount} 景` : ''}
      bboxes={bboxes}
      geojson={coverageGeojson}
      height={280}
      emptyText="当前序列缺少可绘制范围。"
    />
  );
}

/*
function UnusedSceneFootprintGeographicCoverageMap({ coverage }) {
  const mapElementRef = useRef(null);
  const mapRef = useRef(null);
  const tileLayerRef = useRef(null);
  const layerGroupRef = useRef(null);
  const bbox = normalizeBbox(coverage?.bbox);
  const monitorPoints = Array.isArray(coverage?.monitor_points) ? coverage.monitor_points : [];
  const sceneFootprints = useMemo(
    () => normalizeFeatureCollection(coverage?.scene_footprints_geojson),
    [coverage],
  );
  const coverageGeojson = useMemo(
    () => normalizeCoverageGeojson(coverage?.geojson),
    [coverage],
  );
  const sceneFeatureCount = sceneFootprints.features.length;
  const coverageFeatureCount = coverageGeojson.features.length;

  useEffect(() => {
    if (!mapElementRef.current || !bbox) return undefined;
    if (!mapRef.current) {
      mapRef.current = L.map(mapElementRef.current, {
        attributionControl: false,
        zoomControl: false,
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
    const stackBounds = L.latLngBounds([bbox.min_lat, bbox.min_lon], [bbox.max_lat, bbox.max_lon]);

    L.rectangle(stackBounds, {
      color: '#475569',
      weight: 1,
      dashArray: '5 5',
      fillOpacity: 0,
    }).addTo(layerGroup);

    let fitBounds = stackBounds;
    if (sceneFeatureCount > 0) {
      const sceneLayer = L.geoJSON(sceneFootprints, {
        style: feature => {
          const date = String(feature?.properties?.date || '');
          const tone = date.endsWith('22') || date.endsWith('17') ? '#2563eb' : '#0891b2';
          return {
            color: tone,
            weight: 1.6,
            opacity: 0.9,
            fillColor: tone,
            fillOpacity: 0.12,
          };
        },
        onEachFeature: (feature, layer) => {
          const label = featureLabel(feature);
          if (label) {
            layer.bindTooltip(label, { sticky: true });
          }
        },
      }).addTo(layerGroup);
      const sceneBounds = sceneLayer.getBounds();
      if (sceneBounds.isValid()) {
        fitBounds = sceneBounds;
      }
    } else {
      L.rectangle(stackBounds, {
        color: '#2563eb',
        weight: 2,
        fillColor: '#38bdf8',
        fillOpacity: 0.12,
      }).addTo(layerGroup);
    }

    if (coverageFeatureCount > 0) {
      L.geoJSON(coverageGeojson, {
        style: coveragePolygonStyle,
        pointToLayer: coveragePointMarker,
        onEachFeature: (feature, layer) => {
          const label = coverageFeatureLabel(feature);
          if (label) {
            layer.bindTooltip(label, { sticky: true });
          }
        },
      }).addTo(layerGroup);
    }

    monitorPoints.forEach(point => {
      const lon = Number(point.lon);
      const lat = Number(point.lat);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
      L.circleMarker([lat, lon], {
        radius: 5,
        color: '#7c3aed',
        weight: 2,
        fillColor: '#ffffff',
        fillOpacity: 1,
      })
        .bindTooltip(String(point.point_id || 'monitor point'), { direction: 'top' })
        .addTo(layerGroup);
    });

    map.fitBounds(fitBounds.pad(0.12), { animate: false, maxZoom: 12 });
    window.setTimeout(() => map.invalidateSize(), 0);
    return undefined;
  }, [bbox, coverageFeatureCount, coverageGeojson, monitorPoints, sceneFeatureCount, sceneFootprints]);

  useEffect(() => () => {
    if (mapRef.current) {
      mapRef.current.remove();
      mapRef.current = null;
      tileLayerRef.current = null;
      layerGroupRef.current = null;
    }
  }, []);

  if (!bbox) {
    return (
      <div
        style={{
          height: 180,
          display: 'grid',
          placeItems: 'center',
          border: '1px solid #d8dee8',
          borderRadius: 8,
          background: '#f8fafc',
          color: '#64748b',
          fontSize: 12,
        }}
      >
        暂无可展示的地理范围
      </div>
    );
  }

  return (
    <div>
      <div
        ref={mapElementRef}
        style={{
          height: 180,
          border: '1px solid #d8dee8',
          borderRadius: 8,
          overflow: 'hidden',
          background: '#eef2f7',
        }}
      />
      <div style={{ ...mutedStyle, display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 6 }}>
        <span><strong style={{ color: '#2563eb' }}>Blue</strong> scene footprints ({sceneFeatureCount})</span>
        <span><strong style={{ color: '#16a34a' }}>Green dashed</strong> coverage GeoJSON ({coverageFeatureCount})</span>
        <span><strong style={{ color: '#475569' }}>Gray dashed</strong> outer bbox</span>
        <span><strong style={{ color: '#7c3aed' }}>Purple</strong> monitor points</span>
      </div>
    </div>
  );
}

function UnusedGeographicCoveragePanel({ coverage }) {
  const bbox = normalizeBbox(coverage?.bbox);
  const intersection = normalizeBbox(coverage?.bbox_intersection);
  const center = coverage?.center || bboxCenter(bbox);
  const monitorPoints = Array.isArray(coverage?.monitor_points) ? coverage.monitor_points : [];
  const geojsonText = coverage?.geojson ? JSON.stringify(coverage.geojson) : '';

  if (!bbox) {
    return (
      <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10, background: '#f8fafc' }}>
        <div style={valueStyle}>地理范围</div>
        <div style={{ ...mutedStyle, marginTop: 6 }}>
          当前 Run 尚未找到 LT1 元数据 bbox。后续按行政区/AOI 生产时会在这里显示范围。
        </div>
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #99f6e4', borderRadius: 8, padding: 10, background: '#f0fdfa' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
        <div style={valueStyle}>地理范围</div>
        <span style={mutedStyle}>EPSG:4326 / GeoJSON</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 360px) minmax(0, 1fr)', gap: 10, marginTop: 8 }}>
        <SceneFootprintGeographicCoverageMap coverage={coverage} />
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={metricGridStyle}>
            <Metric label="Stack bbox" value={formatBbox(bbox)} />
            <Metric label="交集 bbox" value={formatBbox(intersection)} />
            <Metric
              label="中心点"
              value={center ? `${formatCoord(center.lon)}, ${formatCoord(center.lat)}` : '-'}
            />
            <Metric label="单景范围数" value={`${(coverage?.scene_footprints_geojson?.features || []).length || coverage?.scene_bbox_count || 0}`} />
            <Metric label="范围来源" value={(coverage?.scene_footprints_geojson?.features || []).length > 0 ? 'scene GeoJSON' : 'stack bbox'} />
            <Metric label="监测点" value={`${monitorPoints.length}`} />
          </div>
          {monitorPoints.length > 0 && (
            <div style={{ ...mutedStyle, wordBreak: 'break-word' }}>
              监测点：{monitorPoints.map(point => `${point.point_id || 'point'} (${formatCoord(point.lon)}, ${formatCoord(point.lat)})`).join('；')}
            </div>
          )}
          {geojsonText && (
            <details>
              <summary style={{ ...mutedStyle, cursor: 'pointer', fontWeight: 650 }}>查看 GeoJSON</summary>
              <pre
                style={{
                  margin: '6px 0 0',
                  maxHeight: 120,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  border: '1px solid #ccfbf1',
                  borderRadius: 8,
                  padding: 8,
                  background: '#ffffff',
                  color: '#334155',
                  fontSize: 11,
                  lineHeight: 1.45,
                }}
              >
                {geojsonText}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
*/

const SBAS_FOCUS_TO_SECTION = {
  planning: 'sbas-planning-section',
  batches: 'sbas-run-section',
  prepare: 'sbas-prepare-section',
  runs: 'sbas-run-section',
};

export default function SbasInsarProductionPanel({ readOnly = false, onTaskStart, initialFocus = 'planning' }) {
  const lastAppliedFocusRef = useRef('');
  const [processorMode, setProcessorMode] = useState('landsar');
  const [capabilities, setCapabilities] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [runDetail, setRunDetail] = useState(null);
  const [landsarRuns, setLandsarRuns] = useState([]);
  const [selectedLandsarRunId, setSelectedLandsarRunId] = useState('');
  const [landsarRunDetail, setLandsarRunDetail] = useState(null);
  const [landsarDemPath, setLandsarDemPath] = useState('');
  const [landsarMinScenes, setLandsarMinScenes] = useState(3);
  const [landsarSubmitLoading, setLandsarSubmitLoading] = useState(false);
  const [landsarWorkflowJobLoading, setLandsarWorkflowJobLoading] = useState(false);
  const [landsarWorkflowJob, setLandsarWorkflowJob] = useState(null);
  const [landsarParams, setLandsarParams] = useState({
    dem_format: 4,
    intf_method: 0,
    perp_baseline: 200,
    time_baseline: 300,
    doppler_baseline: 100,
    az_looks: 3,
    rg_looks: 3,
    da_threshold: 0.25,
    network_type: 0,
    solve_method: 0,
    gen_vector_map: false,
    gen_post_raster: true,
  });
  const [loading, setLoading] = useState(false);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [discovering, setDiscovering] = useState(false);
  const [stackCandidates, setStackCandidates] = useState([]);
  const [selectedStackId, setSelectedStackId] = useState('');
  const [auditLoading, setAuditLoading] = useState(false);
  const [stackAudit, setStackAudit] = useState(null);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [stackAdminRegionQuery, setStackAdminRegionQuery] = useState('');
  const [selectedSensorFamily, setSelectedSensorFamily] = useState('LT1');
  const [baselineAuditLoading, setBaselineAuditLoading] = useState(false);
  const [itabDecisionLoading, setItabDecisionLoading] = useState(false);
  const [coregistrationLoading, setCoregistrationLoading] = useState(false);
  const [coregistrationJobLoading, setCoregistrationJobLoading] = useState(false);
  const [coregistrationJob, setCoregistrationJob] = useState(null);
  const [rdcDemLoading, setRdcDemLoading] = useState(false);
  const [rdcDemJobLoading, setRdcDemJobLoading] = useState(false);
  const [rdcDemJob, setRdcDemJob] = useState(null);
  const [interferogramLoading, setInterferogramLoading] = useState(false);
  const [interferogramJobLoading, setInterferogramJobLoading] = useState(false);
  const [interferogramJob, setInterferogramJob] = useState(null);
  const [iptaTimeseriesLoading, setIptaTimeseriesLoading] = useState(false);
  const [iptaTimeseriesJobLoading, setIptaTimeseriesJobLoading] = useState(false);
  const [iptaTimeseriesJob, setIptaTimeseriesJob] = useState(null);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowJobLoading, setWorkflowJobLoading] = useState(false);
  const [workflowJob, setWorkflowJob] = useState(null);
  const [runDeleteLoading, setRunDeleteLoading] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sectionId = SBAS_FOCUS_TO_SECTION[initialFocus] || SBAS_FOCUS_TO_SECTION.planning;
    const focusToken = [
      processorMode,
      initialFocus,
      selectedRunId,
      selectedLandsarRunId,
      stackCandidates.length,
      runs.length,
      landsarRuns.length,
    ].join(':');
    if (lastAppliedFocusRef.current === focusToken) return undefined;
    lastAppliedFocusRef.current = focusToken;
    const timer = window.setTimeout(() => {
      document.getElementById(sectionId)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [
    initialFocus,
    landsarRuns.length,
    processorMode,
    runs.length,
    selectedLandsarRunId,
    selectedRunId,
    stackCandidates.length,
  ]);

  const stackDiscoveryPayload = useMemo(() => {
    const adminRegion = stackAdminRegionQuery.trim();
    const isLandsar = processorMode === 'landsar';
    const processorCapability = (capabilities?.processors || []).find(item =>
      isLandsar ? item.processor_code === 'landsar_sbas' : item.processor_code === 'gamma_ipta_sbas'
    );
    const configuredMinCommonOverlap = Number(
      processorCapability?.min_common_overlap_ratio ?? capabilities?.min_common_overlap_ratio ?? 0.3
    );
    return {
      sensor_family: isLandsar ? 'LT1' : selectedSensorFamily,
      min_scenes: isLandsar ? Math.max(3, Number(landsarMinScenes) || 3) : 3,
      require_orbits: !isLandsar,
      include_scenes: false,
      limit: 0,
      discovery_mode: adminRegion ? 'aoi' : 'strict',
      admin_region: adminRegion || undefined,
      min_aoi_coverage_ratio: 0.01,
      min_common_overlap_ratio: Number.isFinite(configuredMinCommonOverlap) ? configuredMinCommonOverlap : 0.3,
    };
  }, [capabilities, landsarMinScenes, processorMode, selectedSensorFamily, stackAdminRegionQuery]);

  const loadProductionRuns = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [capabilityData, runData] = await Promise.all([
        getSbasInsarCapabilities(),
        listSbasInsarRuns(),
      ]);
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      const activeRun = runItems.find(item => isActiveRunStatus(item.status));
      setCapabilities(capabilityData);
      setRuns(runItems);
      setSelectedRunId(current => {
        if (activeRun && current !== activeRun.run_id) return activeRun.run_id;
        if (current && runItems.some(item => item.run_id === current)) return current;
        return runItems[0]?.run_id || '';
      });
      const landsarCapability = (capabilityData?.processors || []).find(item => item.processor_code === 'landsar_sbas');
      if (landsarCapability) {
        setLandsarDemPath(current => current || landsarCapability.default_dem_path || '');
        setLandsarMinScenes(current => Math.max(3, Number(current || landsarCapability.min_scenes || 3)));
      }
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 列表加载失败');
      setCapabilities(null);
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProductionRuns();
  }, [loadProductionRuns]);

  useEffect(() => {
    const hasActiveRun = runs.some(item => isActiveRunStatus(item.status));
    if (!hasActiveRun) return undefined;
    const timer = window.setInterval(() => {
      loadProductionRuns();
    }, ACTIVE_RUN_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadProductionRuns, runs]);

  const loadRunDetail = useCallback(async runId => {
    if (!runId) {
      setRunDetail(null);
      return;
    }
    setRunDetailLoading(true);
    setError('');
    try {
      const data = await getSbasInsarRun(runId);
      setRunDetail(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 生产 Run 详情加载失败');
      setRunDetail(null);
    } finally {
      setRunDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRunDetail(selectedRunId);
  }, [loadRunDetail, selectedRunId]);

  useEffect(() => {
    const statusText = String(runDetail?.run?.status || runDetail?.manifest?.status || '').toUpperCase();
    const active = Boolean(runDetail?.runtime_status?.active) || statusText.includes('RUNNING');
    if (!selectedRunId || !active) return undefined;
    const timer = window.setInterval(() => {
      loadRunDetail(selectedRunId);
    }, ACTIVE_RUN_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadRunDetail, runDetail?.manifest?.status, runDetail?.run?.status, runDetail?.runtime_status?.active, selectedRunId]);

  const loadLandsarRuns = useCallback(async () => {
    setError('');
    try {
      const data = await listLandsarSbasRuns();
      const items = Array.isArray(data?.items) ? data.items : [];
      setLandsarRuns(items);
      setSelectedLandsarRunId(current => current || items[0]?.run_id || '');
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'LandSAR SBAS Run 列表加载失败');
      setLandsarRuns([]);
    }
  }, []);

  useEffect(() => {
    loadLandsarRuns();
  }, [loadLandsarRuns]);

  const loadLandsarRunDetail = useCallback(async runId => {
    if (!runId) {
      setLandsarRunDetail(null);
      return;
    }
    setRunDetailLoading(true);
    setError('');
    try {
      const data = await getLandsarSbasRun(runId);
      setLandsarRunDetail(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'LandSAR SBAS Run 详情加载失败');
      setLandsarRunDetail(null);
    } finally {
      setRunDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (processorMode === 'landsar') {
      loadLandsarRunDetail(selectedLandsarRunId);
    }
  }, [loadLandsarRunDetail, processorMode, selectedLandsarRunId]);

  const handleDiscoverStacks = useCallback(async () => {
    setDiscovering(true);
    setError('');
    setStackAudit(null);
    try {
      const data = await discoverSbasInsarStacks(stackDiscoveryPayload);
      const items = Array.isArray(data?.items) ? data.items : [];
      setStackCandidates(items);
      setSelectedStackId(items[0]?.stack_id || '');
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 栈发现失败');
      setStackCandidates([]);
    } finally {
      setDiscovering(false);
    }
  }, [stackDiscoveryPayload]);

  const handleAuditStack = useCallback(async stackId => {
    if (!stackId) return;
    setAuditLoading(true);
    setError('');
    try {
      const data = await auditSbasInsarStack(stackId, {
        ...stackDiscoveryPayload,
        include_scenes: true,
      });
      setStackAudit(data);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 栈审查失败');
      setStackAudit(null);
    } finally {
      setAuditLoading(false);
    }
  }, [stackDiscoveryPayload]);

  const handleSubmitRun = useCallback(async () => {
    if (!selectedStackId || readOnly) return;
    setSubmitLoading(true);
    setError('');
    try {
      const candidate = stackCandidates.find(item => item.stack_id === selectedStackId);
      const data = await submitSbasInsarRun(selectedStackId, {
        ...stackDiscoveryPayload,
        run_label: candidate
          ? `${candidate.satellite || selectedSensorFamily} ${formatAdminRegion(candidate.admin_region)} relOrbit ${candidate.relative_orbit || ''}`.trim()
          : undefined,
        dry_run: false,
        monitor_point_strategy: 'auto_representative_points',
      });
      const runId = data?.run?.run_id;
      const runData = await listSbasInsarRuns();
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      setRuns(runItems);
      if (runId) {
        setSelectedRunId(runId);
        setRunDetail(data);
      }
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 生产 Run 创建失败');
    } finally {
      setSubmitLoading(false);
    }
  }, [readOnly, selectedSensorFamily, selectedStackId, stackCandidates, stackDiscoveryPayload]);

  const handleDeleteRun = useCallback(async runId => {
    if (!runId || readOnly || runDeleteLoading) return;
    const target = runs.find(item => item.run_id === runId);
    const label = target?.run_label || target?.run_id || runId;
    const ok = window.confirm(
      `确定删除生产 Run ${label}？\n\n会删除运行目录、关联任务/Job 记录和已登记的 SBAS 成果。正在运行的任务会被后端拒绝删除。`
    );
    if (!ok) return;
    setRunDeleteLoading(true);
    setError('');
    try {
      await deleteSbasInsarRun(runId);
      const runData = await listSbasInsarRuns();
      const runItems = Array.isArray(runData?.items) ? runData.items : [];
      setRuns(runItems);
      const nextRunId = selectedRunId === runId ? (runItems[0]?.run_id || '') : selectedRunId;
      setSelectedRunId(nextRunId);
      if (nextRunId) {
        const detailData = await getSbasInsarRun(nextRunId);
        setRunDetail(detailData);
      } else {
        setRunDetail(null);
      }
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 生产 Run 删除失败');
    } finally {
      setRunDeleteLoading(false);
    }
  }, [readOnly, runDeleteLoading, runs, selectedRunId]);

  const handleSubmitLandsarAutoWorkflow = useCallback(async () => {
    if (readOnly) return;
    setLandsarWorkflowJobLoading(true);
    setLandsarSubmitLoading(true);
    setError('');
    setStackAudit(null);
    try {
      const data = await submitLandsarSbasAutoWorkflow({
        ...stackDiscoveryPayload,
        sensor_family: 'LT1',
        require_orbits: false,
        include_scenes: false,
        dem_path: landsarDemPath || undefined,
        timeout_seconds: 172800,
        import_timeout_seconds: 172800,
        workflow_timeout_seconds: 172800,
        params: landsarParams,
      });
      const runId = data?.run_id || data?.run?.run_id || data?.manifest?.run_id;
      const selection = data?.selection || {};
      const selected = selection.selected_stack;
      if (selected?.stack_id) {
        setStackCandidates(selection.ranked_candidates || [selected]);
        setSelectedStackId(selected.stack_id);
      }
      if (runId) {
        setSelectedLandsarRunId(runId);
        const detailData = await getLandsarSbasRun(runId);
        setLandsarRunDetail(detailData);
      }
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'LandSAR SBAS Workflow 已入队。', {
          taskType: data.job_type || 'SBAS_LANDSAR_WORKFLOW',
          nonBlocking: true,
        });
      }
      setLandsarWorkflowJob(data);
      await loadLandsarRuns();
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'LandSAR SBAS 自动生产提交失败');
      setLandsarWorkflowJob(null);
    } finally {
      setLandsarSubmitLoading(false);
      setLandsarWorkflowJobLoading(false);
    }
  }, [landsarDemPath, landsarParams, loadLandsarRuns, onTaskStart, readOnly, stackDiscoveryPayload]);

  const workflowPayload = useMemo(() => ({
    force: false,
    rlks: 8,
    azlks: 8,
    reference_window: 16,
    mb_mode: 0,
    timeout_seconds: 172800,
  }), []);

  const handlePrepareWorkflow = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setWorkflowLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarWorkflow(selectedRunId, workflowPayload);
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'Gamma SBAS workflow 生成失败');
    } finally {
      setWorkflowLoading(false);
    }
  }, [readOnly, selectedRunId, workflowPayload]);

  const handleSubmitWorkflowJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setWorkflowJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarWorkflowJob(selectedRunId, workflowPayload);
      setWorkflowJob(data);
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'Gamma SBAS Workflow 已入队。', {
          taskType: data.job_type || 'SBAS_GAMMA_WORKFLOW',
          nonBlocking: true,
        });
      }
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'Gamma SBAS workflow 任务提交失败');
      setWorkflowJob(null);
    } finally {
      setWorkflowJobLoading(false);
    }
  }, [onTaskStart, readOnly, selectedRunId, workflowPayload]);

  const handleBaselineAudit = useCallback(async (execute = false) => {
    if (!selectedRunId || readOnly) return;
    setBaselineAuditLoading(true);
    setError('');
    try {
      const data = await runSbasInsarBaselineAudit(selectedRunId, {
        execute,
        rlks: 8,
        azlks: 8,
        max_delta_n: 1,
        timeout_seconds: 21600,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR baseline audit 失败');
    } finally {
      setBaselineAuditLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleItabDecision = useCallback(async decision => {
    if (!selectedRunId || readOnly) return;
    setItabDecisionLoading(true);
    setError('');
    try {
      const data = await decideSbasInsarItab(selectedRunId, {
        decision,
        reviewer: 'ui',
        note: decision === 'approve'
          ? 'Baseline-audited adjacent itab accepted from SBAS production page.'
          : 'Baseline-audited adjacent itab rejected from SBAS production page.',
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR itab 审批失败');
    } finally {
      setItabDecisionLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handlePrepareCoregistration = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setCoregistrationLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarCoregistration(selectedRunId, {
        execute: false,
        rlks: 8,
        azlks: 8,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 共参考配准计划生成失败');
    } finally {
      setCoregistrationLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitCoregistrationJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setCoregistrationJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarCoregistrationJob(selectedRunId, {
        rlks: 8,
        azlks: 8,
        timeout_seconds: 43200,
      });
      setCoregistrationJob(data);
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'SBAS 共参考配准 Task 已入队。', {
          taskType: data.job_type || 'SBAS_COREGISTRATION',
          nonBlocking: true,
        });
      }
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR 共参考配准任务提交失败');
      setCoregistrationJob(null);
    } finally {
      setCoregistrationJobLoading(false);
    }
  }, [onTaskStart, readOnly, selectedRunId]);

  const handlePrepareRdcDem = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setRdcDemLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarRdcDem(selectedRunId, {
        execute: false,
        rlks: 8,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR RDC DEM 计划生成失败');
    } finally {
      setRdcDemLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitRdcDemJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setRdcDemJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarRdcDemJob(selectedRunId, {
        rlks: 8,
        timeout_seconds: 43200,
      });
      setRdcDemJob(data);
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'SBAS RDC DEM Task 已入队。', {
          taskType: data.job_type || 'SBAS_RDC_DEM',
          nonBlocking: true,
        });
      }
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR RDC DEM 任务提交失败');
      setRdcDemJob(null);
    } finally {
      setRdcDemJobLoading(false);
    }
  }, [onTaskStart, readOnly, selectedRunId]);

  const handlePrepareInterferograms = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setInterferogramLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarInterferograms(selectedRunId, {
        execute: false,
        rlks: 8,
        azlks: 8,
        unwrap_threshold: 0.2,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR interferogram 计划生成失败');
    } finally {
      setInterferogramLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitInterferogramsJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setInterferogramJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarInterferogramsJob(selectedRunId, {
        rlks: 8,
        azlks: 8,
        unwrap_threshold: 0.2,
        timeout_seconds: 43200,
      });
      setInterferogramJob(data);
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'SBAS 干涉图 Task 已入队。', {
          taskType: data.job_type || 'SBAS_INTERFEROGRAMS',
          nonBlocking: true,
        });
      }
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR interferogram 任务提交失败');
      setInterferogramJob(null);
    } finally {
      setInterferogramJobLoading(false);
    }
  }, [onTaskStart, readOnly, selectedRunId]);

  const handlePrepareIptaTimeseries = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setIptaTimeseriesLoading(true);
    setError('');
    try {
      const data = await prepareSbasInsarIptaTimeseries(selectedRunId, {
        execute: false,
        rlks: 8,
        reference_window: 16,
      });
      setRunDetail(data);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR IPTA timeseries 计划生成失败');
    } finally {
      setIptaTimeseriesLoading(false);
    }
  }, [readOnly, selectedRunId]);

  const handleSubmitIptaTimeseriesJob = useCallback(async () => {
    if (!selectedRunId || readOnly) return;
    setIptaTimeseriesJobLoading(true);
    setError('');
    try {
      const data = await submitSbasInsarIptaTimeseriesJob(selectedRunId, {
        rlks: 8,
        reference_window: 16,
        timeout_seconds: 43200,
      });
      setIptaTimeseriesJob(data);
      if (data?.task_id) {
        onTaskStart?.(data.task_id, 'SBAS IPTA 时序 Task 已入队。', {
          taskType: data.job_type || 'SBAS_IPTA_TIMESERIES',
          nonBlocking: true,
        });
      }
      const detailData = await getSbasInsarRun(selectedRunId);
      setRunDetail(detailData);
      const runData = await listSbasInsarRuns();
      setRuns(Array.isArray(runData?.items) ? runData.items : []);
    } catch (exc) {
      setError(exc?.response?.data?.detail || exc.message || 'SBAS-InSAR IPTA timeseries 任务提交失败');
      setIptaTimeseriesJob(null);
    } finally {
      setIptaTimeseriesJobLoading(false);
    }
  }, [onTaskStart, readOnly, selectedRunId]);

  const selectedStack = stackCandidates.find(item => item.stack_id === selectedStackId) || null;
  const run = runDetail?.run || null;
  const runManifest = runDetail?.manifest || {};
  const workflowManifest = runDetail?.workflow_manifest || {};
  const workflowState = runDetail?.workflow_state || {};
  const runtimeStatus = runDetail?.runtime_status || null;
  const workflowSteps = Array.isArray(workflowManifest.steps) ? workflowManifest.steps : [];
  const expertDocumentSteps = Array.isArray(workflowManifest.expert_document?.steps)
    ? workflowManifest.expert_document.steps
    : [];
  const workflowStepState = workflowState.steps || {};
  const stagePlan = runDetail?.command_manifest?.stage_plan || [];
  const runArtifacts = runDetail?.artifacts || [];
  const baselineSummary = runManifest.baseline_audit?.summary || null;
  const itabDecision = runManifest.baseline_audit?.itab_decision || null;
  const coregistrationPlan = runManifest.coregistration || null;
  const rdcDemPlan = runManifest.rdc_dem || null;
  const interferogramPlan = runManifest.interferograms || null;
  const detrendAtmPlan = runManifest.detrend_atm || null;
  const iptaTimeseriesPlan = runManifest.ipta_timeseries || null;
  const publishProductsPlan = runManifest.publish_products || null;
  const monitorProductsPlan = runManifest.monitor_point_products || null;
  const runGeographicCoverage = runDetail?.geographic_coverage || null;
  const itabApproved = itabDecision?.decision === 'approve' || runManifest.baseline_audit?.approved_for_next_stage === true;
  const itabRejected = itabDecision?.decision === 'reject';
  const runSensorFamily = String(run?.sensor_family || runManifest.sensor_family || runManifest.profile_code || '').toUpperCase();
  const runExecutionEnabled = run?.execution_enabled !== false && runManifest.execution_enabled !== false && !runSensorFamily.startsWith('S1');
  const showGammaAdvancedActions = runManifest.show_advanced_actions === true;
  const landsarCapability = (capabilities?.processors || []).find(item => item.processor_code === 'landsar_sbas') || {};
  const landsarRun = landsarRunDetail?.run || null;
  const landsarManifest = landsarRunDetail?.manifest || {};
  const landsarWorkflow = landsarRunDetail?.workflow_manifest || {};
  const landsarArtifacts = landsarRunDetail?.artifacts || [];
  const landsarPrimaryPreview = landsarArtifacts.find(item => item.relative_path === 'publish/landsar/preview.png');
  const landsarPrimaryTif = landsarArtifacts.find(item => item.relative_path === 'publish/landsar/los_timeseries.tif');
  const activeGammaRun = runs.find(item => isActiveRunStatus(item.status)) || null;
  const activeGammaRunNotice = activeGammaRun ? (
    <div style={{ marginTop: 10, border: '1px solid #bae6fd', borderRadius: 8, background: '#f0f9ff', padding: 10, display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
      <div>
        <div style={valueStyle}>Gamma run is active</div>
        <div style={{ ...mutedStyle, marginTop: 4 }}>
          {activeGammaRun.run_label || activeGammaRun.run_id}；{activeGammaRun.status}
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          setSelectedRunId(activeGammaRun.run_id);
          setProcessorMode('gamma');
        }}
        style={{ border: '1px solid #0369a1', borderRadius: 8, background: '#e0f2fe', color: '#0369a1', padding: '7px 11px', fontWeight: 750 }}
      >
        打开运行状态
      </button>
    </div>
  ) : null;
  const updateLandsarParam = (key, value) => {
    setLandsarParams(current => ({ ...current, [key]: value }));
  };
  const processorSelector = (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
      {[
        ['landsar', 'LandSAR SBAS'],
        ['gamma', 'Gamma / IPTA SBAS'],
      ].map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => setProcessorMode(key)}
          style={{
            border: `1px solid ${processorMode === key ? '#0f766e' : '#cbd5e1'}`,
            borderRadius: 8,
            background: processorMode === key ? '#ccfbf1' : '#ffffff',
            color: processorMode === key ? '#0f766e' : '#334155',
            padding: '8px 12px',
            fontWeight: 750,
            cursor: 'pointer',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );

  if (processorMode === 'landsar') {
    return (
      <div style={shellStyle}>
        <section style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 20, color: '#0f172a' }}>SBAS-InSAR 生产</h2>
              <div style={{ ...mutedStyle, marginTop: 6 }}>
                LandSAR SBAS 按生产区域自动发现 LT-1 时序栈、创建 Run，并在后台导入场景后执行一体化流程。
              </div>
              {processorSelector}
            </div>
            <button
              type="button"
              onClick={() => {
                loadProductionRuns();
                loadLandsarRuns();
              }}
              disabled={loading}
              style={{
                border: '1px solid #0f766e',
                borderRadius: 8,
                background: '#f0fdfa',
                color: '#0f766e',
                padding: '8px 12px',
                fontWeight: 700,
                cursor: loading ? 'default' : 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {loading ? '刷新中' : '刷新'}
            </button>
          </div>
          <div style={{ ...metricGridStyle, marginTop: 12 }}>
            <Metric label="处理器" value={landsarCapability.processor_code || 'landsar_sbas'} />
            <Metric label="引擎" value={landsarCapability.engine_code || 'landsar'} />
            <Metric label="状态" value={landsarCapability.status || '-'} />
            <Metric label="最少景数" value={landsarCapability.min_scenes || landsarMinScenes} />
          </div>
          {error && (
            <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 8, border: '1px solid #fecaca', background: '#fef2f2', color: '#991b1b', fontSize: 13 }}>
              {error}
            </div>
          )}
        </section>

        <section id="sbas-planning-section" style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>SBAS 生产区域</h3>
              <div style={{ ...mutedStyle, marginTop: 5 }}>
                只需要指定生产区域；系统会自动寻找满足覆盖和时序条件的 LT-1 栈。下方候选仅用于审计，不需要人工选序列。
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <span style={{ ...labelStyle, border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', background: '#fff' }}>LT-1</span>
              <input
                value={stackAdminRegionQuery}
                onChange={event => {
                  setStackAdminRegionQuery(event.target.value);
                  setStackCandidates([]);
                  setSelectedStackId('');
                  setStackAudit(null);
                }}
                onKeyDown={event => {
                  if (event.key === 'Enter') handleSubmitLandsarAutoWorkflow();
                }}
                placeholder="输入行政区，例如 牡丹江 / 洛阳"
                style={{
                  border: '1px solid #cbd5e1',
                  borderRadius: 8,
                  padding: '8px 10px',
                  fontSize: 12,
                  minWidth: 180,
                }}
              />
              <button
                type="button"
                onClick={handleDiscoverStacks}
                disabled={discovering}
                style={{
                  border: '1px solid #0f766e',
                  borderRadius: 8,
                  background: '#f0fdfa',
                  color: '#0f766e',
                  padding: '8px 12px',
                  fontWeight: 700,
                  cursor: discovering ? 'default' : 'pointer',
                  whiteSpace: 'nowrap',
                }}
              >
                {discovering ? '审计中' : '审计候选'}
              </button>
              {!readOnly && (
                <button
                  type="button"
                  onClick={handleSubmitLandsarAutoWorkflow}
                  disabled={landsarWorkflowJobLoading || landsarSubmitLoading}
                  style={{
                    border: '1px solid #7c3aed',
                    borderRadius: 8,
                    background: '#f5f3ff',
                    color: '#6d28d9',
                    padding: '8px 12px',
                    fontWeight: 700,
                    cursor: landsarWorkflowJobLoading || landsarSubmitLoading ? 'default' : 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {landsarWorkflowJobLoading || landsarSubmitLoading ? '提交中' : '自动创建并提交'}
                </button>
              )}
            </div>
          </div>

          <div id="sbas-prepare-section" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginTop: 12 }}>
            <label style={{ display: 'grid', gap: 5, gridColumn: 'span 2' }}>
              <span style={labelStyle}>DEM 文件</span>
              <input value={landsarDemPath} onChange={event => setLandsarDemPath(event.target.value)} placeholder="D:\\DEM\\HeiLongJiang10M_DEM.tif" style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>最少景数</span>
              <input type="number" value={landsarMinScenes} min={3} onChange={event => setLandsarMinScenes(event.target.value)} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>干涉对方法</span>
              <select value={landsarParams.intf_method} onChange={event => updateLandsarParam('intf_method', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12, background: '#fff' }}>
                <option value={0}>single</option>
                <option value={1}>prim</option>
              </select>
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>垂直基线</span>
              <input type="number" value={landsarParams.perp_baseline} onChange={event => updateLandsarParam('perp_baseline', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>时间基线</span>
              <input type="number" value={landsarParams.time_baseline} onChange={event => updateLandsarParam('time_baseline', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>多普勒基线</span>
              <input type="number" value={landsarParams.doppler_baseline} onChange={event => updateLandsarParam('doppler_baseline', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>方位向多视</span>
              <input type="number" min={1} value={landsarParams.az_looks} onChange={event => updateLandsarParam('az_looks', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>距离向多视</span>
              <input type="number" min={1} value={landsarParams.rg_looks} onChange={event => updateLandsarParam('rg_looks', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
            <label style={{ display: 'grid', gap: 5 }}>
              <span style={labelStyle}>DA 阈值</span>
              <input type="number" step="0.01" min={0} max={1} value={landsarParams.da_threshold} onChange={event => updateLandsarParam('da_threshold', Number(event.target.value))} style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '8px 10px', fontSize: 12 }} />
            </label>
          </div>

          {stackCandidates.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
              <div style={{ display: 'grid', gap: 8, maxHeight: 360, overflow: 'auto' }}>
                {stackCandidates.map(item => {
                  const active = item.stack_id === selectedStackId;
                  return (
                    <div
                      key={item.stack_id}
                      style={{
                        ...buttonBaseStyle,
                        borderColor: active ? '#1d4ed8' : '#d8dee8',
                        background: active ? '#eff6ff' : '#ffffff',
                        cursor: 'default',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                        <strong style={{ color: '#0f172a', fontSize: 13 }}>
                          {item.satellite || 'LT1'} / {item.orbit_direction || '-'} / relOrbit {item.relative_orbit || '-'}
                        </strong>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                          <StatusBadge value={item.sensor_family || 'LT1'} />
                          <StatusBadge value={item.status} />
                        </div>
                      </div>
                      <div style={{ ...mutedStyle, marginTop: 6 }}>
                        {item.date_start} 至 {item.date_end}，可用 {item.usable_scene_count}/{item.scene_count} 景，最大间隔 {item.max_temporal_gap_days} 天
                      </div>
                      <div style={{ ...mutedStyle, marginTop: 4 }}>
                        行政区：{formatAdminRegion(item.admin_region)}；公共重叠 {formatPercent(item.common_overlap_ratio)}
                      </div>
                      <div style={{ ...mutedStyle, marginTop: 4 }}>
                        影像组 {shortHash(item.scene_identity_hash)}；同日期序列 {item.same_date_sequence_candidate_count || 1} 组
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: 'grid', gap: 10 }}>
                {selectedStack && (
                  <>
                    <div style={metricGridStyle}>
                      <Metric label="平台/模式" value={`${selectedStack.satellite || '-'} / ${selectedStack.imaging_mode || '-'}`} />
                      <Metric label="轨道方向" value={selectedStack.orbit_direction || '-'} />
                      <Metric label="极化/接收站" value={`${selectedStack.polarization || '-'} / ${selectedStack.receiving_station || '-'}`} />
                      <Metric label="建议参考日期" value={selectedStack.reference_date || '-'} />
                      <Metric label="公共重叠" value={formatPercent(selectedStack.common_overlap_ratio)} />
                      <Metric label="最低公共重叠" value={formatPercent(selectedStack.min_common_overlap_ratio)} />
                      <Metric label="AOI 覆盖" value={formatPercent(selectedStack.aoi_overlap_ratio_mean)} />
                  </div>
                  {(selectedStack.blockers || []).length > 0 && (
                    <div style={{ marginTop: 8, border: '1px solid #fecaca', borderRadius: 8, padding: 9, background: '#fef2f2', color: '#991b1b', fontSize: 12, lineHeight: 1.5 }}>
                      Blocked: {selectedStack.blockers.join('; ')}
                    </div>
                  )}
                  <StackIdentityNotice stack={selectedStack} />
                  <SceneNamePanel stack={selectedStack} />
                  <StackCoverageMiniMap stack={selectedStack} title="LandSAR SBAS序列范围预览" />
                  <LocationSummaryPanel
                    coverage={{
                        bbox: selectedStack.bbox || selectedStack.bbox_intersection,
                        bbox_intersection: selectedStack.bbox_intersection,
                        center: selectedStack.center || bboxCenter(selectedStack.bbox || selectedStack.bbox_intersection),
                        admin_region: selectedStack.admin_region,
                        scene_bbox_count: selectedStack.usable_scene_count || selectedStack.scene_count || 0,
                      }}
                    />
                  </>
                )}
                {stackAudit && (
                  <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                    <div style={valueStyle}>Manifest 已生成</div>
                    <div style={{ ...mutedStyle, marginTop: 6, wordBreak: 'break-all' }}>
                      {stackAudit.manifest_path}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      状态：{stackAudit.status}；pair 数：{stackAudit.manifest?.pair_network?.pairs?.length || 0}
                    </div>
                    {(stackAudit.manifest?.warnings || []).length > 0 && (
                      <div style={{ ...mutedStyle, marginTop: 6 }}>
                        警告：{stackAudit.manifest.warnings.join('；')}
                      </div>
                    )}
                  </div>
                )}
                {landsarRun && (
                  <div style={{ border: '1px solid #ddd6fe', borderRadius: 8, padding: 10, background: '#f5f3ff' }}>
                    <div style={valueStyle}>LandSAR Run</div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      {landsarRun.run_id}；状态：{landsarRun.status}；下一步：{landsarRun.next_stage || '-'}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
          {!discovering && stackCandidates.length === 0 && (
            <div style={{ ...mutedStyle, padding: '10px 0', marginTop: 8 }}>提交后后台任务会自动发现并选择生产序列；候选审计只用于提前查看系统会如何筛选。</div>
          )}
          {landsarWorkflowJob?.selection_pending && (
            <div style={{ ...mutedStyle, border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4', marginTop: 10 }}>
              已提交后台自动生产任务：{landsarWorkflowJob.task_id || '-'}；Job：{landsarWorkflowJob.job_id || '-'}。系统正在复用 Gamma 的生产区域栈发现与审计逻辑选择 LT-1 序列，随后自动导入并执行 LandSAR SBAS。
            </div>
          )}
        </section>

        <section id="sbas-run-section" style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>LandSAR Run</h3>
              <div style={{ ...mutedStyle, marginTop: 5 }}>自动生产任务会创建 Run、导入选中序列并执行 LandSAR SBAS，完成后结果会进入 SBAS-InSAR 结果目录。</div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
            <div style={{ display: 'grid', gap: 8, maxHeight: 320, overflow: 'auto' }}>
              {landsarRuns.map(item => {
                const active = item.run_id === selectedLandsarRunId;
                return (
                  <button key={item.run_id} type="button" onClick={() => setSelectedLandsarRunId(item.run_id)} style={{ ...buttonBaseStyle, borderColor: active ? '#7c3aed' : '#d8dee8', background: active ? '#f5f3ff' : '#fff' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong style={{ color: '#0f172a', fontSize: 13 }}>{item.run_label || item.run_id}</strong>
                      <StatusBadge value={item.status} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>{item.scene_count || 0} 景，{item.task_count || 0} 个 Task，{item.date_start || '-'} 至 {item.date_end || '-'}</div>
                    <div style={{ ...mutedStyle, marginTop: 4, wordBreak: 'break-all' }}>{item.run_id}</div>
                  </button>
                );
              })}
              {!loading && landsarRuns.length === 0 && <div style={{ ...mutedStyle, padding: '10px 0' }}>暂无 LandSAR SBAS Run。</div>}
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {runDetailLoading && <div style={mutedStyle}>正在加载 Run 详情...</div>}
              {!runDetailLoading && landsarRun && (
                <>
                  <div style={metricGridStyle}>
                    <Metric label="状态" value={landsarRun.status || '-'} />
                    <Metric label="Task 数" value={landsarRun.task_count || landsarManifest.task_count || 0} />
                    <Metric label="场景数" value={landsarRun.scene_count || landsarManifest.scene_count || 0} />
                    <Metric label="下一阶段" value={landsarRun.next_stage || '-'} />
                  </div>
                  {landsarPrimaryPreview && (
                    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', background: '#0f172a', maxWidth: 520 }}>
                      <img src={getLandsarSbasRunArtifactUrl(landsarRun.run_id, landsarPrimaryPreview.relative_path)} alt={landsarRun.run_label || landsarRun.run_id} style={{ display: 'block', width: '100%', objectFit: 'contain' }} />
                    </div>
                  )}
                  {landsarWorkflowJob && landsarWorkflowJob.run_id === landsarRun.run_id && (
                    <div style={{ ...mutedStyle, border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4' }}>
                      已提交后台任务：{landsarWorkflowJob.task_id}；Job：{landsarWorkflowJob.job_id}
                    </div>
                  )}
                  <div style={{ display: 'grid', gap: 6 }}>
                    {(landsarArtifacts.slice(0, 24)).map(asset => (
                      <div key={asset.relative_path} style={{ display: 'grid', gridTemplateColumns: 'minmax(130px, 180px) minmax(0, 1fr) auto', gap: 8, alignItems: 'center', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 9px' }}>
                        <div style={{ fontSize: 12, fontWeight: 750, color: '#0f172a' }}>{asset.role}</div>
                        <div style={{ ...mutedStyle, wordBreak: 'break-all' }}>{asset.relative_path}</div>
                        <a href={getLandsarSbasRunArtifactUrl(landsarRun.run_id, asset.relative_path)} target="_blank" rel="noreferrer" style={{ color: '#1d4ed8', fontSize: 12, fontWeight: 750 }}>打开</a>
                      </div>
                    ))}
                  </div>
                  {landsarPrimaryTif && (
                    <div style={mutedStyle}>主 GeoTIFF：{landsarPrimaryTif.relative_path}</div>
                  )}
                  {landsarWorkflow?.task_results && (
                    <div style={mutedStyle}>完成 {landsarWorkflow.completed_count || 0}，失败 {landsarWorkflow.failed_count || 0}</div>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div style={shellStyle}>
      <section id="sbas-prepare-section" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 20, color: '#0f172a' }}>SBAS-InSAR 生产</h2>
            <div style={{ ...mutedStyle, marginTop: 6 }}>
              Gamma IPTA SBAS 生产入口。当前阶段接入已验证的 LT1/Gamma 试验成果，作业提交在下一阶段开放。
            </div>
            {processorSelector}
          </div>
          <button
            type="button"
            onClick={loadProductionRuns}
            disabled={loading}
            style={{
              border: '1px solid #0f766e',
              borderRadius: 8,
              background: '#f0fdfa',
              color: '#0f766e',
              padding: '8px 12px',
              fontWeight: 700,
              cursor: loading ? 'default' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {loading ? '刷新中' : '刷新'}
          </button>
        </div>
        {capabilities && (
          <div style={{ ...metricGridStyle, marginTop: 12 }}>
            <Metric label="处理器" value={capabilities.processor_code || '-'} />
            <Metric label="引擎" value={capabilities.engine_code || '-'} />
            <Metric label="阶段" value={capabilities.implementation_state || '-'} />
            <Metric label="默认 LOS" value={capabilities.default_los_convention?.description || '-'} />
          </div>
        )}
        {readOnly && (
          <div style={{ ...mutedStyle, marginTop: 10 }}>
            当前账号只读，生产提交按钮不会显示。
          </div>
        )}
        {error && (
          <div
            style={{
              marginTop: 10,
              padding: '8px 10px',
              borderRadius: 8,
              border: '1px solid #fecaca',
              background: '#fef2f2',
              color: '#991b1b',
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}
        {activeGammaRunNotice}
      </section>

      <section id="sbas-planning-section" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>SBAS 生产区域</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              按生产行政区查找覆盖同一目标区域的 LT1 时序候选，并检查日期密度、精轨和公共重叠范围。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <select
              value={selectedSensorFamily}
              onChange={event => {
                setSelectedSensorFamily(event.target.value);
                setStackCandidates([]);
                setSelectedStackId('');
                setStackAudit(null);
              }}
              style={{
                border: '1px solid #cbd5e1',
                borderRadius: 8,
                padding: '8px 10px',
                fontSize: 12,
                minWidth: 130,
                background: '#fff',
                color: '#0f172a',
              }}
            >
              <option value="LT1">LT-1</option>
              <option value="S1">Sentinel-1</option>
            </select>
            <input
              value={stackAdminRegionQuery}
              onChange={event => {
                setStackAdminRegionQuery(event.target.value);
                setStackCandidates([]);
                setSelectedStackId('');
                setStackAudit(null);
              }}
              onKeyDown={event => {
                if (event.key === 'Enter') handleDiscoverStacks();
              }}
              placeholder="输入行政区，例如 牡丹江 / 洛阳"
              style={{
                border: '1px solid #cbd5e1',
                borderRadius: 8,
                padding: '8px 10px',
                fontSize: 12,
                minWidth: 160,
              }}
            />
            <button
              type="button"
              onClick={handleDiscoverStacks}
              disabled={discovering}
              style={{
                border: '1px solid #0f766e',
                borderRadius: 8,
                background: '#f0fdfa',
                color: '#0f766e',
                padding: '8px 12px',
                fontWeight: 700,
                cursor: discovering ? 'default' : 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {discovering ? '查找中' : '查找候选'}
            </button>
            <button
              type="button"
              onClick={() => handleAuditStack(selectedStackId)}
              disabled={!selectedStackId || auditLoading}
              style={{
                border: '1px solid #1d4ed8',
                borderRadius: 8,
                background: selectedStackId ? '#eff6ff' : '#f8fafc',
                color: selectedStackId ? '#1d4ed8' : '#94a3b8',
                padding: '8px 12px',
                fontWeight: 700,
                cursor: selectedStackId && !auditLoading ? 'pointer' : 'default',
                whiteSpace: 'nowrap',
              }}
            >
              {auditLoading ? '审查中' : '生成 Manifest'}
            </button>
            {!readOnly && (
              <button
                type="button"
                onClick={handleSubmitRun}
                disabled={!selectedStackId || submitLoading}
                style={{
                  border: '1px solid #7c3aed',
                  borderRadius: 8,
                  background: selectedStackId ? '#f5f3ff' : '#f8fafc',
                  color: selectedStackId ? '#6d28d9' : '#94a3b8',
                  padding: '8px 12px',
                  fontWeight: 700,
                  cursor: selectedStackId && !submitLoading ? 'pointer' : 'default',
                  whiteSpace: 'nowrap',
                }}
              >
                {submitLoading ? '创建中' : '创建计划 Run'}
              </button>
            )}
          </div>
        </div>

        {stackCandidates.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
            <div style={{ display: 'grid', gap: 8, maxHeight: 360, overflow: 'auto' }}>
              {stackCandidates.map(item => {
                const active = item.stack_id === selectedStackId;
                return (
                  <button
                    key={item.stack_id}
                    type="button"
                    onClick={() => setSelectedStackId(item.stack_id)}
                    style={{
                      ...buttonBaseStyle,
                      borderColor: active ? '#1d4ed8' : '#d8dee8',
                      background: active ? '#eff6ff' : '#ffffff',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <strong style={{ color: '#0f172a', fontSize: 13 }}>
                        {item.satellite || 'LT1'} / {item.orbit_direction || '-'} / relOrbit {item.relative_orbit || '-'}
                      </strong>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        <StatusBadge value={item.sensor_family || selectedSensorFamily} />
                        <StatusBadge value={item.status} />
                      </div>
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      {item.date_start} 至 {item.date_end}，可用 {item.usable_scene_count}/{item.scene_count} 景，
                      缺精轨 {item.missing_orbit_count}，最大间隔 {item.max_temporal_gap_days} 天
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 4 }}>
                      行政区：{formatAdminRegion(item.admin_region)}；公共重叠 {formatPercent(item.common_overlap_ratio)}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 4 }}>
                      覆盖 {formatPercent(item.aoi_overlap_ratio_mean)}；中心点 {formatCenter(item.center)}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 4 }}>
                      影像组 {shortHash(item.scene_identity_hash)}；同日期序列 {item.same_date_sequence_candidate_count || 1} 组
                    </div>
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {selectedStack && (
                <>
                  <div style={metricGridStyle}>
                    <Metric label="平台/模式" value={`${selectedStack.satellite || '-'} / ${selectedStack.imaging_mode || '-'}`} />
                    <Metric label="轨道方向" value={selectedStack.orbit_direction || '-'} />
                    <Metric label="极化/接收站" value={`${selectedStack.polarization || '-'} / ${selectedStack.receiving_station || '-'}`} />
                    <Metric label="建议参考日期" value={selectedStack.reference_date || '-'} />
                    <Metric label="公共重叠" value={formatPercent(selectedStack.common_overlap_ratio)} />
                    <Metric label="最低公共重叠" value={formatPercent(selectedStack.min_common_overlap_ratio)} />
                    <Metric label="AOI 覆盖" value={formatPercent(selectedStack.aoi_overlap_ratio_mean)} />
                  </div>
                  {(selectedStack.blockers || []).length > 0 && (
                    <div style={{ marginTop: 8, border: '1px solid #fecaca', borderRadius: 8, padding: 9, background: '#fef2f2', color: '#991b1b', fontSize: 12, lineHeight: 1.5 }}>
                      Blocked: {selectedStack.blockers.join('; ')}
                    </div>
                  )}
                  <StackIdentityNotice stack={selectedStack} />
                  <SceneNamePanel stack={selectedStack} />
                  <StackCoverageMiniMap stack={selectedStack} title="Gamma SBAS序列范围预览" />
                  <LocationSummaryPanel
                    coverage={{
                      bbox: selectedStack.bbox || selectedStack.bbox_intersection,
                      bbox_intersection: selectedStack.bbox_intersection,
                      center: selectedStack.center || bboxCenter(selectedStack.bbox || selectedStack.bbox_intersection),
                      admin_region: selectedStack.admin_region,
                      scene_bbox_count: selectedStack.usable_scene_count || selectedStack.scene_count || 0,
                    }}
                  />
                </>
              )}
              {stackAudit && (
                <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                  <div style={valueStyle}>Manifest 已生成</div>
                  <div style={{ ...mutedStyle, marginTop: 6, wordBreak: 'break-all' }}>
                    {stackAudit.manifest_path}
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    状态：{stackAudit.status}；pair 数：
                    {stackAudit.manifest?.pair_network?.pairs?.length || 0}
                  </div>
                  {(stackAudit.manifest?.warnings || []).length > 0 && (
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      警告：{stackAudit.manifest.warnings.join('；')}
                    </div>
                  )}
                  {(stackAudit.manifest?.blockers || []).length > 0 && (
                    <div style={{ ...mutedStyle, marginTop: 6, color: '#991b1b' }}>
                      阻断：{stackAudit.manifest.blockers.join('；')}
                    </div>
                  )}
                </div>
              )}
              {run && (
                <div style={{ border: '1px solid #ddd6fe', borderRadius: 8, padding: 10, background: '#f5f3ff' }}>
                  <div style={valueStyle}>计划 Run 已创建</div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {run.run_id}；状态：{run.status}；下一步：{run.next_stage || '-'}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <section id="sbas-run-section" style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, color: '#0f172a' }}>生产 Run 计划</h3>
            <div style={{ ...mutedStyle, marginTop: 5 }}>
              当前只创建可复现实验记录、Gamma 命令计划和监测点配置；正式执行在 baseline 审核后接入。
            </div>
          </div>
          <span style={mutedStyle}>{runs.length} 个</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 420px) minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
          <div style={{ display: 'grid', gap: 8, maxHeight: 300, overflow: 'auto' }}>
            {runs.map(item => {
              const active = item.run_id === selectedRunId;
              const running = isActiveRunStatus(item.status);
              return (
                <button
                  key={item.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(item.run_id)}
                  style={{
                    ...buttonBaseStyle,
                    borderColor: active ? '#7c3aed' : '#d8dee8',
                    background: active ? '#f5f3ff' : '#ffffff',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong style={{ color: '#0f172a', fontSize: 13 }}>
                      {formatAdminRegion(item.admin_region)} / {item.platform || 'LT1'} / relOrbit {item.relative_orbit || '-'}
                    </strong>
                    <StatusBadge value={item.status} />
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {item.scene_count || 0} 景，{item.pair_count || 0} 对，下一步 {item.next_stage || '-'}
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 4 }}>
                    影像组 {shortHash(item.scene_identity_hash)}
                  </div>
                  {running && (
                    <div style={{ marginTop: 6, color: '#0369a1', fontSize: 12, fontWeight: 700 }}>
                      正在运行，已自动打开右侧运行状态
                    </div>
                  )}
                </button>
              );
            })}
            {!loading && runs.length === 0 && (
              <div style={{ ...mutedStyle, padding: '10px 0' }}>
                暂无计划 Run。先发现序列，再创建计划 Run。
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gap: 10 }}>
            {runDetailLoading && <div style={mutedStyle}>正在加载 Run 详情...</div>}
            {!runDetailLoading && run && (
              <>
                <div
                  style={{
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    padding: 10,
                    background: '#f8fafc',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 10,
                    flexWrap: 'wrap',
                  }}
                >
                  <div>
                    <div style={valueStyle}>{run.run_label || run.run_id}</div>
                    <div style={{ ...mutedStyle, marginTop: 4, wordBreak: 'break-all' }}>
                      Run ID: {run.run_id}
                    </div>
                  </div>
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() => handleDeleteRun(run.run_id)}
                      disabled={runDeleteLoading}
                      style={{
                        border: '1px solid #b91c1c',
                        borderRadius: 8,
                        background: runDeleteLoading ? '#f8fafc' : '#fef2f2',
                        color: runDeleteLoading ? '#94a3b8' : '#b91c1c',
                        padding: '7px 11px',
                        fontWeight: 700,
                        cursor: runDeleteLoading ? 'default' : 'pointer',
                      }}
                    >
                      {runDeleteLoading ? '删除中' : '删除 Run'}
                    </button>
                  )}
                </div>

                <div style={metricGridStyle}>
                  <Metric label="状态" value={run.status || '-'} />
                  <Metric label="参考日期" value={run.reference_date || '-'} />
                  <Metric label="场景/配对" value={`${run.scene_count || 0} / ${run.pair_count || 0}`} />
                  <Metric label="下一阶段" value={run.next_stage || '-'} />
                  <Metric label="影像组" value={shortHash(run.scene_identity_hash)} />
                  <Metric
                    label="公共重叠"
                    value={`${formatPercent(run.common_overlap_ratio)} / ${formatPercent(run.min_common_overlap_ratio)}`}
                  />
                </div>

                <SceneNamePanel stack={{ ...run, scenes: runManifest.scenes }} />

                <RuntimeStatusPanel status={runtimeStatus} />

                <details style={compactDetailsStyle}>
                  <summary style={compactSummaryStyle}>空间覆盖</summary>
                  <div style={{ marginTop: 10 }}>
                    <StackCoverageMiniMap coverage={runGeographicCoverage} title="Run覆盖范围预览" />
                  </div>
                  <div style={{ marginTop: 10 }}>
                    <LocationSummaryPanel coverage={runGeographicCoverage} />
                  </div>
                </details>

                {!readOnly && (
                  <div style={{ border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4' }}>
                    <div style={valueStyle}>Gamma SBAS Workflow</div>
                    {!runExecutionEnabled && (
                      <div style={{
                        marginTop: 8,
                        padding: '8px 10px',
                        borderRadius: 8,
                        border: '1px solid #fed7aa',
                        background: '#fff7ed',
                        color: '#9a3412',
                        fontSize: 12,
                      }}>
                        Sentinel-1 Gamma SBAS 当前仅开放规划能力：可进行栈发现、审计 Manifest 和 Run 记录管理；Gamma 执行需等待 S1 TOPS/SBAS 脚本验证完成后启用。
                      </div>
                    )}
                    <div style={{ ...mutedStyle, marginTop: 6 }}>
                      专家文档目录 + manifest + WSL runner 主路径，生产执行以当前统一工作流为准。
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                      <button
                        type="button"
                        onClick={handlePrepareWorkflow}
                        disabled={workflowLoading || !runExecutionEnabled}
                        style={{
                          border: '1px solid #15803d',
                          borderRadius: 8,
                          background: '#dcfce7',
                          color: '#166534',
                          padding: '8px 12px',
                          fontWeight: 700,
                          cursor: workflowLoading || !runExecutionEnabled ? 'default' : 'pointer',
                        }}
                      >
                        {workflowLoading ? '生成中' : '生成 Workflow Manifest'}
                      </button>
                      <button
                        type="button"
                        onClick={handleSubmitWorkflowJob}
                        disabled={workflowJobLoading || !runExecutionEnabled}
                        style={{
                          border: '1px solid #0f766e',
                          borderRadius: 8,
                          background: '#ccfbf1',
                          color: '#0f766e',
                          padding: '8px 12px',
                          fontWeight: 700,
                          cursor: workflowJobLoading || !runExecutionEnabled ? 'default' : 'pointer',
                        }}
                      >
                        {workflowJobLoading ? '提交中' : '提交 Gamma SBAS Workflow'}
                      </button>
                    </div>
                    {workflowJob && workflowJob.run_id === run.run_id && (
                      <div style={{ ...mutedStyle, marginTop: 8 }}>
                        已提交后台任务：{workflowJob.task_id}；Job：{workflowJob.job_id}
                      </div>
                    )}
                    {workflowSteps.length > 0 && (
                      <details style={{ ...compactDetailsStyle, marginTop: 10, borderColor: '#bbf7d0' }}>
                        <summary style={compactSummaryStyle}>Workflow steps ({workflowSteps.length})</summary>
                        <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
                        {workflowSteps.map(step => {
                          const state = workflowStepState[step.id] || {};
                          return (
                            <div
                              key={step.id}
                              style={{
                                display: 'grid',
                                gridTemplateColumns: 'minmax(190px, 1fr) minmax(120px, auto)',
                                gap: 8,
                                alignItems: 'center',
                                border: '1px solid #bbf7d0',
                                borderRadius: 8,
                                padding: '8px 10px',
                                background: '#ffffff',
                              }}
                            >
                              <div>
                                <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>
                                  {step.id} · {step.name}
                                </div>
                                <div style={mutedStyle}>
                                  {(step.expert_tools || []).join(', ') || 'planned'}；{step.enabled ? 'enabled' : 'planned'}
                                </div>
                              </div>
                              <StatusBadge value={state.status || step.status || 'PENDING'} />
                            </div>
                          );
                        })}
                        </div>
                      </details>
                    )}
                    {expertDocumentSteps.length > 0 && (
                      <details style={{ ...compactDetailsStyle, marginTop: 10 }}>
                        <summary style={compactSummaryStyle}>Expert document path ({expertDocumentSteps.length})</summary>
                        <div style={{ ...mutedStyle, marginTop: 4 }}>
                          已载入 LT1 Gamma SBAS 专家文档中的 {expertDocumentSteps.length} 个章节。命令清单作为验收检查项，已完成的 Workflow 步骤必须通过专家命令审计。
                        </div>
                        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
                          {expertDocumentSteps.map(item => {
                            const mappedStatuses = (item.mapped_workflow_steps || [])
                              .map(mapped => {
                                const state = workflowStepState[mapped.id] || {};
                                return state.status || mapped.status;
                              })
                              .filter(Boolean);
                            const displayStatus = mappedStatuses[0] || item.implementation_status || 'planned';
                            const commandPreview = (item.commands || []).slice(0, 3).join(' | ');
                            return (
                              <div
                                key={item.id}
                                style={{
                                  display: 'grid',
                                  gridTemplateColumns: 'minmax(210px, 1fr) minmax(120px, auto)',
                                  gap: 8,
                                  alignItems: 'start',
                                  border: '1px solid #e2e8f0',
                                  borderRadius: 8,
                                  padding: '8px 10px',
                                  background: item.enabled ? '#ffffff' : '#f8fafc',
                                }}
                              >
                                <div>
                                  <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>
                                    {item.order}. {item.title}
                                  </div>
                                  <div style={mutedStyle}>
                                    maps to {(item.workflow_steps || []).join(', ') || '-'}; {item.command_count || 0} commands; {item.implementation_status}
                                  </div>
                                  {commandPreview && (
                                    <div style={{ ...mutedStyle, marginTop: 3, fontFamily: 'monospace', overflowWrap: 'anywhere' }}>
                                      {commandPreview}
                                    </div>
                                  )}
                                </div>
                                <StatusBadge value={displayStatus} />
                              </div>
                            );
                          })}
                        </div>
                      </details>
                    )}
                  </div>
                )}

                {!readOnly && showGammaAdvancedActions && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() => handleBaselineAudit(false)}
                      disabled={baselineAuditLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #1d4ed8',
                        borderRadius: 8,
                        background: '#eff6ff',
                        color: '#1d4ed8',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: baselineAuditLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {baselineAuditLoading ? '处理中' : '生成/解析 baseline audit'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleBaselineAudit(true)}
                      disabled={baselineAuditLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #7c3aed',
                        borderRadius: 8,
                        background: '#f5f3ff',
                        color: '#6d28d9',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: baselineAuditLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      执行 Gamma baseline audit
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareCoregistration}
                      disabled={coregistrationLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #0f766e',
                        borderRadius: 8,
                        background: '#f0fdfa',
                        color: '#0f766e',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: coregistrationLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {coregistrationLoading ? '生成中' : '生成共参考配准脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitCoregistrationJob}
                      disabled={coregistrationJobLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #b45309',
                        borderRadius: 8,
                        background: '#fffbeb',
                        color: '#92400e',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: coregistrationJobLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {coregistrationJobLoading ? '提交中' : '提交共参考配准任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareRdcDem}
                      disabled={rdcDemLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #0369a1',
                        borderRadius: 8,
                        background: '#f0f9ff',
                        color: '#0369a1',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: rdcDemLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {rdcDemLoading ? '生成中' : '生成 RDC DEM 脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitRdcDemJob}
                      disabled={rdcDemJobLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #4338ca',
                        borderRadius: 8,
                        background: '#eef2ff',
                        color: '#3730a3',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: rdcDemJobLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {rdcDemJobLoading ? '提交中' : '提交 RDC DEM 任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareInterferograms}
                      disabled={interferogramLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #7c2d12',
                        borderRadius: 8,
                        background: '#fff7ed',
                        color: '#7c2d12',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: interferogramLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {interferogramLoading ? '生成中' : '生成干涉图脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitInterferogramsJob}
                      disabled={interferogramJobLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #be123c',
                        borderRadius: 8,
                        background: '#fff1f2',
                        color: '#be123c',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: interferogramJobLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {interferogramJobLoading ? '提交中' : '提交干涉图任务'}
                    </button>
                    <button
                      type="button"
                      onClick={handlePrepareIptaTimeseries}
                      disabled={iptaTimeseriesLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #166534',
                        borderRadius: 8,
                        background: '#f0fdf4',
                        color: '#166534',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: iptaTimeseriesLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {iptaTimeseriesLoading ? '生成中' : '生成 IPTA 脚本'}
                    </button>
                    <button
                      type="button"
                      onClick={handleSubmitIptaTimeseriesJob}
                      disabled={iptaTimeseriesJobLoading || !runExecutionEnabled}
                      style={{
                        border: '1px solid #15803d',
                        borderRadius: 8,
                        background: '#dcfce7',
                        color: '#166534',
                        padding: '8px 12px',
                        fontWeight: 700,
                        cursor: iptaTimeseriesJobLoading || !runExecutionEnabled ? 'default' : 'pointer',
                      }}
                    >
                      {iptaTimeseriesJobLoading ? '提交中' : '提交 IPTA 任务'}
                    </button>
                  </div>
                )}

                <details style={compactDetailsStyle}>
                  <summary style={compactSummaryStyle}>阶段/脚本明细</summary>
                  <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                  <div style={valueStyle}>Gamma 阶段计划</div>
                  <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
                    {stagePlan.map(stage => (
                      <div
                        key={stage.stage_id}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'minmax(160px, 1fr) minmax(120px, auto)',
                          gap: 8,
                          alignItems: 'center',
                          padding: '7px 8px',
                          border: '1px solid #f1f5f9',
                          borderRadius: 8,
                          background: '#f8fafc',
                        }}
                      >
                        <div>
                          <div style={{ color: '#0f172a', fontSize: 13, fontWeight: 650 }}>{stage.label}</div>
                          <div style={mutedStyle}>{(stage.gamma_tools || []).join(', ') || '应用内产品处理'}</div>
                        </div>
                        <StatusBadge value={stage.status} />
                      </div>
                    ))}
                  </div>
                </div>

                {baselineSummary && (
                  <div style={{ border: '1px solid #dbeafe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                    <div style={valueStyle}>Baseline Audit 结果</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="全部配对" value={`${baselineSummary.all_pair_count || 0}`} />
                      <Metric label="相邻配对" value={`${baselineSummary.adjacent_pair_count || 0}`} />
                      <Metric label="最大 |Bperp|" value={formatValue(baselineSummary.max_abs_bperp_m, ' m')} />
                      <Metric label="最大时间间隔" value={formatValue(baselineSummary.max_delta_days, ' d')} />
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        gap: 10,
                        flexWrap: 'wrap',
                        alignItems: 'center',
                        marginTop: 10,
                      }}
                    >
                      <div style={mutedStyle}>
                        itab 状态：
                        {itabDecision
                          ? `${itabDecision.decision} / ${itabDecision.decided_at || '-'}`
                          : '等待审批'}
                      </div>
                      {!readOnly && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <button
                            type="button"
                            onClick={() => handleItabDecision('approve')}
                            disabled={itabDecisionLoading || itabApproved || !runExecutionEnabled}
                            style={{
                              border: '1px solid #0f766e',
                              borderRadius: 8,
                              background: itabApproved ? '#f8fafc' : '#f0fdfa',
                              color: itabApproved ? '#94a3b8' : '#0f766e',
                              padding: '7px 11px',
                              fontWeight: 700,
                              cursor: itabDecisionLoading || itabApproved || !runExecutionEnabled ? 'default' : 'pointer',
                            }}
                          >
                            {itabApproved ? 'itab 已批准' : '批准 itab'}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleItabDecision('reject')}
                            disabled={itabDecisionLoading || itabApproved || itabRejected || !runExecutionEnabled}
                            style={{
                              border: '1px solid #b91c1c',
                              borderRadius: 8,
                              background: itabApproved || itabRejected ? '#f8fafc' : '#fef2f2',
                              color: itabApproved || itabRejected ? '#94a3b8' : '#b91c1c',
                              padding: '7px 11px',
                              fontWeight: 700,
                              cursor: itabDecisionLoading || itabApproved || itabRejected || !runExecutionEnabled ? 'default' : 'pointer',
                            }}
                          >
                            {itabRejected ? 'itab 已拒绝' : '拒绝 itab'}
                          </button>
                        </div>
                      )}
                    </div>
                    <div style={{ overflowX: 'auto', marginTop: 10 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                          <tr style={{ color: '#475569' }}>
                            <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>Pair</th>
                            <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>日期</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>Bperp</th>
                            <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #bfdbfe' }}>间隔</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(baselineSummary.adjacent_pairs || []).map(pair => (
                            <tr key={`${pair.master_date}_${pair.slave_date}`}>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe' }}>{pair.pair_index}</td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe' }}>
                                {pair.master_date}{' -> '}{pair.slave_date}
                              </td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe', textAlign: 'right' }}>
                                {formatValue(pair.bperp_m, ' m')}
                              </td>
                              <td style={{ padding: '6px 8px', borderBottom: '1px solid #dbeafe', textAlign: 'right' }}>
                                {formatValue(pair.delta_days, ' d')}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {coregistrationPlan && (
                  <div style={{ border: '1px solid #ccfbf1', borderRadius: 8, padding: 10, background: '#f0fdfa' }}>
                    <div style={valueStyle}>共参考配准计划</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="参考日期" value={coregistrationPlan.reference_date || '-'} />
                      <Metric label="场景数" value={`${coregistrationPlan.scene_count || 0}`} />
                      <Metric label="批准配对" value={`${coregistrationPlan.approved_pair_count || 0}`} />
                      <Metric label="多视参数" value={`${coregistrationPlan.rlks || '-'} / ${coregistrationPlan.azlks || '-'}`} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      脚本：{coregistrationPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      输出目录：{coregistrationPlan.outputs?.common_dir || '-'}
                    </div>
                    {coregistrationPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        执行状态：{coregistrationPlan.execution.status || '-'}；
                        {coregistrationPlan.execution.ended_at || coregistrationPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {coregistrationPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        RSLC 就绪：
                        {coregistrationPlan.summary.ready_secondary_count || 0}/
                        {coregistrationPlan.summary.expected_secondary_count || 0}；
                        缺失日期：{(coregistrationPlan.summary.missing_dates || []).join(', ') || '无'}
                      </div>
                    )}
                    {coregistrationJob && coregistrationJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #fde68a',
                          borderRadius: 8,
                          background: '#fffbeb',
                          color: '#92400e',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        已提交后台任务：{coregistrationJob.task_id}；Job：{coregistrationJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: 10 }}>
                  <div style={valueStyle}>监测点配置</div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    模式：{runDetail.monitor_points?.mode || '-'}；
                    点数：{runDetail.monitor_points?.points?.length || 0}
                  </div>
                  <div style={{ ...mutedStyle, marginTop: 6 }}>
                    {runDetail.monitor_points?.note || '等待产品生成后提取时序曲线。'}
                  </div>
                </div>

                {rdcDemPlan && (
                  <div style={{ border: '1px solid #bfdbfe', borderRadius: 8, padding: 10, background: '#eff6ff' }}>
                    <div style={valueStyle}>RDC DEM Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={rdcDemPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${rdcDemPlan.rlks || '-'}`} />
                      <Metric
                        label="DEM covers center"
                        value={rdcDemPlan.dem_source?.covers_stack_center === false ? 'No' : 'Yes'}
                      />
                      <Metric
                        label="DEM covers bbox"
                        value={rdcDemPlan.dem_source?.covers_stack_bbox ? 'Yes' : 'Partial'}
                      />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {rdcDemPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DEM source: {rdcDemPlan.dem_source?.windows_path || rdcDemPlan.dem_source?.wsl_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      Output: {rdcDemPlan.outputs?.rdc_dem || '-'}
                    </div>
                    {rdcDemPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {rdcDemPlan.execution.status || '-'};{' '}
                        {rdcDemPlan.execution.ended_at || rdcDemPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {rdcDemPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Ready: {rdcDemPlan.summary.ready ? 'Yes' : 'No'}; missing:{' '}
                        {(rdcDemPlan.summary.missing_outputs || []).join(', ') || 'none'}
                      </div>
                    )}
                    {rdcDemJob && rdcDemJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #c7d2fe',
                          borderRadius: 8,
                          background: '#eef2ff',
                          color: '#3730a3',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {rdcDemJob.task_id}; Job: {rdcDemJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {interferogramPlan && (
                  <div style={{ border: '1px solid #fed7aa', borderRadius: 8, padding: 10, background: '#fff7ed' }}>
                    <div style={valueStyle}>Interferogram Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={interferogramPlan.reference_date || '-'} />
                      <Metric label="Pairs" value={`${interferogramPlan.pair_count || 0}`} />
                      <Metric label="Looks" value={`${interferogramPlan.rlks || '-'} / ${interferogramPlan.azlks || '-'}`} />
                      <Metric label="Threshold" value={`${interferogramPlan.unwrap_threshold ?? '-'}`} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {interferogramPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DIFF_tab: {interferogramPlan.outputs?.diff_tab || '-'}
                    </div>
                    {interferogramPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {interferogramPlan.execution.status || '-'};{' '}
                        {interferogramPlan.execution.ended_at || interferogramPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {interferogramPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Ready pairs: {interferogramPlan.summary.ready_pair_count || 0}/
                        {interferogramPlan.summary.pair_count || 0}; missing:{' '}
                        {(interferogramPlan.summary.missing_pairs || []).join(', ') || 'none'}
                      </div>
                    )}
                    {interferogramJob && interferogramJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #fecdd3',
                          borderRadius: 8,
                          background: '#fff1f2',
                          color: '#be123c',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {interferogramJob.task_id}; Job: {interferogramJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {detrendAtmPlan && (
                  <div style={{ border: '1px solid #fed7aa', borderRadius: 8, padding: 10, background: '#fff7ed' }}>
                    <div style={valueStyle}>Detrend / ATM Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={detrendAtmPlan.reference_date || '-'} />
                      <Metric label="Pairs" value={`${detrendAtmPlan.summary?.ready_pair_count || 0}/${detrendAtmPlan.pair_count || detrendAtmPlan.summary?.pair_count || 0}`} />
                      <Metric label="CC min" value={`${detrendAtmPlan.coherence_min ?? '-'}`} />
                      <Metric label="Ready" value={detrendAtmPlan.summary?.ready ? 'Yes' : '-'} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {detrendAtmPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      DIFF_atmsub_tab: {detrendAtmPlan.outputs?.diff_atmsub_tab || detrendAtmPlan.summary?.outputs?.diff_atmsub_tab?.path || '-'}
                    </div>
                    {detrendAtmPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {detrendAtmPlan.execution.status || '-'};{' '}
                        {detrendAtmPlan.execution.ended_at || detrendAtmPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {detrendAtmPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing pairs: {(detrendAtmPlan.summary.missing_pairs || []).join(', ') || 'none'}; rows:{' '}
                        {detrendAtmPlan.summary.diff_atmsub_tab_row_count || 0}/
                        {detrendAtmPlan.summary.itab_atmsub_row_count || 0}
                      </div>
                    )}
                  </div>
                )}

                {iptaTimeseriesPlan && (
                  <div style={{ border: '1px solid #bbf7d0', borderRadius: 8, padding: 10, background: '#f0fdf4' }}>
                    <div style={valueStyle}>IPTA Time-Series Plan</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={iptaTimeseriesPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${iptaTimeseriesPlan.rlks || '-'}`} />
                      <Metric label="Window" value={`${iptaTimeseriesPlan.reference_window || '-'}`} />
                      <Metric label="Ready" value={iptaTimeseriesPlan.summary?.ready ? 'Yes' : '-'} />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {iptaTimeseriesPlan.script_path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      ts_rate: {iptaTimeseriesPlan.outputs?.ts_rate || iptaTimeseriesPlan.summary?.outputs?.ts_rate?.path || '-'}
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 5, wordBreak: 'break-all' }}>
                      sigma_rate: {iptaTimeseriesPlan.outputs?.sigma_rate || iptaTimeseriesPlan.summary?.outputs?.sigma_rate?.path || '-'}
                    </div>
                    {iptaTimeseriesPlan.execution && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Execution: {iptaTimeseriesPlan.execution.status || '-'};{' '}
                        {iptaTimeseriesPlan.execution.ended_at || iptaTimeseriesPlan.execution.started_at || '-'}
                      </div>
                    )}
                    {iptaTimeseriesPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing: {(iptaTimeseriesPlan.summary.missing_outputs || []).join(', ') || 'none'}; rows:{' '}
                        {iptaTimeseriesPlan.summary.diff_ts_row_count || 0}/
                        {iptaTimeseriesPlan.summary.itab_ts_row_count || 0}
                      </div>
                    )}
                    {iptaTimeseriesJob && iptaTimeseriesJob.run_id === run.run_id && (
                      <div
                        style={{
                          marginTop: 8,
                          border: '1px solid #bbf7d0',
                          borderRadius: 8,
                          background: '#dcfce7',
                          color: '#166534',
                          padding: '7px 9px',
                          fontSize: 12,
                          lineHeight: 1.5,
                          wordBreak: 'break-all',
                        }}
                      >
                        Queued task: {iptaTimeseriesJob.task_id}; Job: {iptaTimeseriesJob.job_id}
                      </div>
                    )}
                  </div>
                )}

                {publishProductsPlan && (
                  <div style={{ border: '1px solid #bae6fd', borderRadius: 8, padding: 10, background: '#f0f9ff' }}>
                    <div style={valueStyle}>Publish Products</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={publishProductsPlan.reference_date || '-'} />
                      <Metric label="RLKS" value={`${publishProductsPlan.rlks || '-'}`} />
                      <Metric label="Ready" value={publishProductsPlan.summary?.ready ? 'Yes' : '-'} />
                      <Metric label="LOS sign" value="Toward positive" />
                    </div>
                    <div style={{ ...mutedStyle, marginTop: 8, wordBreak: 'break-all' }}>
                      Script: {publishProductsPlan.script_path || '-'}
                    </div>
                    {publishProductsPlan.summary && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Missing: {(publishProductsPlan.summary.missing_outputs || []).join(', ') || 'none'}
                      </div>
                    )}
                  </div>
                )}

                {monitorProductsPlan && (
                  <div style={{ border: '1px solid #ddd6fe', borderRadius: 8, padding: 10, background: '#f5f3ff' }}>
                    <div style={valueStyle}>Monitoring Point Products</div>
                    <div style={{ ...metricGridStyle, marginTop: 8 }}>
                      <Metric label="Reference date" value={monitorProductsPlan.reference_date || '-'} />
                      <Metric label="Dates" value={`${monitorProductsPlan.dates?.length || 0}`} />
                      <Metric label="Ready" value={monitorProductsPlan.summary?.ready ? 'Yes' : '-'} />
                      <Metric label="Mode" value={runDetail.monitor_points?.mode || '-'} />
                    </div>
                    {monitorProductsPlan.summary?.monitor_outputs?.length > 0 && (
                      <div style={{ ...mutedStyle, marginTop: 5 }}>
                        Points: {monitorProductsPlan.summary.monitor_outputs.map(item => item.point_id).join(', ')}
                      </div>
                    )}
                  </div>
                )}
                  </div>
                </details>

                {runArtifacts.length > 0 && (
                  <details style={compactDetailsStyle}>
                    <summary style={compactSummaryStyle}>资产下载 ({runArtifacts.length})</summary>
                    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden', marginTop: 10 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <thead>
                        <tr style={{ background: '#f8fafc', color: '#475569' }}>
                          <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>文件</th>
                          <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>角色</th>
                          <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #e2e8f0' }}>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runArtifacts.map(item => (
                          <tr key={item.key}>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#0f172a' }}>{item.label}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', color: '#64748b' }}>{item.role}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid #f1f5f9', textAlign: 'right' }}>
                              <RunArtifactLink runId={run.run_id} artifact={item} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    </div>
                  </details>
                )}
              </>
            )}
            {!runDetailLoading && !run && (
              <div style={mutedStyle}>请选择一个生产 Run，或从候选序列创建新的计划 Run。</div>
            )}
          </div>
        </div>
      </section>

    </div>
  );
}
