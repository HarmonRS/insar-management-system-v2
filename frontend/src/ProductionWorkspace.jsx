import { Suspense, lazy, useEffect, useMemo, useState } from 'react';

import {
  PRODUCTION_WORKSPACE_ENTRY_TO_VIEW,
  PRODUCTION_WORKSPACE_TAB,
  PRODUCTION_WORKSPACE_VIEWS,
  PRODUCTION_WORKSPACE_WORKBENCHES,
} from './config/appConstants';
import { PanelLoadingBody } from './components/app/AppLoadingFallbacks';

const LazyDinsarProductionPanel = lazy(() => import('./DinsarProductionPanel'));
const LazySbasInsarProductionPanel = lazy(() => import('./SbasInsarProductionPanel'));
const LazySbasInsarProductsPanel = lazy(() => import('./SbasInsarProductsPanel'));
const LazyDinsarProductsPanel = lazy(() => import('./DinsarProductsPanel'));
const LazyPairPlanningPanel = lazy(() => import('./panels/PairPlanningPanel'));
const LazyPairsListPanel = lazy(() => import('./panels/PairsListPanel'));
const LazyBatchPanel = lazy(() => import('./panels/BatchPanel'));
const LazyDataCopierPanel = lazy(() => import('./DataCopierPanel'));

const shellStyle = {
  minHeight: '100%',
  padding: '20px 24px 28px',
  boxSizing: 'border-box',
  background: 'linear-gradient(180deg, #f5f7fb 0%, #eef4ff 52%, #f8fafc 100%)',
};

const heroStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
  gap: 16,
  marginBottom: 18,
};

const heroCardStyle = {
  borderRadius: 24,
  border: '1px solid #d7e0eb',
  background: 'linear-gradient(135deg, #ffffff 0%, #f8fbff 56%, #eef6ff 100%)',
  boxShadow: '0 16px 40px rgba(15, 23, 42, 0.06)',
};

const summaryCardStyle = {
  padding: '12px 14px',
  borderRadius: 18,
  border: '1px solid #e2e8f0',
  background: 'rgba(255, 255, 255, 0.82)',
};

const SENSOR_PRODUCTION_PLACEHOLDERS = {
  lt1_production: {
    title: '陆探一号生产模块',
    subtitle: '当前先占位纳入生产管理，执行链路保留 LandSAR、ENVI+SARscape、Gamma/PyINT。',
    rows: [
      ['源压缩包', 'D:\\LuTan1_Image_Pool_Zip，只索引包内 XML/元数据，不做全量解包。'],
      ['精密轨道', 'D:\\LT1_data_lsarorbit，本机部署并绑定到源资产。'],
      ['按需解包', '生产任务需要时才 materialize 到 D:\\Task_Pool\\DInSAR 或 D:\\Task_Pool\\SBAS。'],
      ['生产边界', 'D-InSAR 与 SBAS-InSAR 均使用本机 Task_Pool，不允许 UNC 参与运行。'],
      ['结果管理', '生成结果进入 D-InSAR/SBAS 产物目录，由生产管理结果页统一重建 catalog。'],
    ],
  },
  sentinel1_production: {
    title: 'Sentinel-1 生产模块',
    subtitle: '当前先占位纳入生产管理，D-InSAR 保留 Gamma/PyINT 路径，SBAS 仍为规划态。',
    rows: [
      ['源压缩包', 'D:\\Sentinel1_Image_Pool_ZIP，本机登记 ZIP/SAFE 元数据。'],
      ['精密轨道', 'D:\\Sentinel1_EOF_Pool，本机保存 AUX_POEORB/RESORB。'],
      ['按需解包', '需要运行时才将 ZIP 解包到本机 Task_Pool，界面不提供全量解包按钮。'],
      ['D-InSAR', 'Gamma/PyINT 可作为生产方向，运行材料必须来自本机路径。'],
      ['SBAS', '当前仅做堆栈发现和规划，执行链路未启用。'],
    ],
  },
  gf3_native_registration: {
    title: '高分三结果登记',
    subtitle: 'GF3 不在本机生产；另一台 SARscape 服务器完成 _geo 后复制到本机登记。',
    rows: [
      ['外部生产', '外部机器按 YYYYMMDD_geo/场景目录输出 SARscape 原生 _geo 二进制。'],
      ['本机落盘', '复制到 D:\\GaoFen3_Pool\\native_geo 后递归扫描登记。'],
      ['预览生成', 'WebP 从 *_geo 主二进制读取生成，不使用 *_geo_ql.tif 作为正式预览源。'],
      ['精轨', 'GF3 本链路无精密轨道管理。'],
      ['结果管理', '登记后的 GF3 资产进入数据管理，后续需要全影像时再提取/标准化。'],
    ],
  },
};

function SensorProductionPlaceholder({ viewKey }) {
  const data = SENSOR_PRODUCTION_PLACEHOLDERS[viewKey];
  if (!data) {
    return null;
  }
  return (
    <section style={{ ...heroCardStyle, padding: '18px 20px' }}>
      <div style={{ fontSize: 12, color: '#475569', marginBottom: 8 }}>当前设计约定</div>
      <h3 style={{ margin: '0 0 8px', fontSize: 20, color: '#0f172a' }}>{data.title}</h3>
      <p style={{ margin: '0 0 16px', color: '#475569', fontSize: 13, lineHeight: 1.7 }}>
        {data.subtitle}
      </p>
      <div style={{ display: 'grid', gap: 10 }}>
        {data.rows.map(([label, value]) => (
          <div
            key={label}
            style={{
              display: 'grid',
              gridTemplateColumns: '120px 1fr',
              gap: 12,
              padding: '10px 12px',
              borderRadius: 8,
              border: '1px solid #e2e8f0',
              background: '#fff',
            }}
          >
            <strong style={{ color: '#0f172a', fontSize: 13 }}>{label}</strong>
            <span style={{ color: '#475569', fontSize: 13, lineHeight: 1.7, wordBreak: 'break-word' }}>{value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function resolveView(entry) {
  return PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[entry] || PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[PRODUCTION_WORKSPACE_TAB];
}

function resolveWorkbenchKey(viewKey) {
  const workbench = PRODUCTION_WORKSPACE_WORKBENCHES.find(item => (
    item.views.some(view => view.key === viewKey)
  ));
  return workbench?.key || PRODUCTION_WORKSPACE_WORKBENCHES[0]?.key || 'dinsar_workbench';
}

export default function ProductionWorkspace({
  activeEntry = PRODUCTION_WORKSPACE_TAB,
  readOnly = false,
  onTaskStart,
  apiEndpoint,
  language,
  foundPairs = [],
  selectedPairsCount = 0,
  isLoading = false,
  hasEnoughRadarScenesForPlanning = false,
  hasRadarSearched = false,
  pairingPanel = {},
  radarPanel = {},
  pairsPanel = {},
}) {
  const [activeView, setActiveView] = useState(() => resolveView(activeEntry));
  const [activeWorkbench, setActiveWorkbench] = useState(() => resolveWorkbenchKey(resolveView(activeEntry)));

  useEffect(() => {
    const nextView = resolveView(activeEntry);
    setActiveView(nextView);
    setActiveWorkbench(resolveWorkbenchKey(nextView));
  }, [activeEntry]);

  const activeViewMeta = useMemo(
    () => PRODUCTION_WORKSPACE_VIEWS.find(view => view.key === activeView) || PRODUCTION_WORKSPACE_VIEWS[0],
    [activeView]
  );
  const activeWorkbenchMeta = useMemo(
    () => PRODUCTION_WORKSPACE_WORKBENCHES.find(item => item.key === activeWorkbench) || PRODUCTION_WORKSPACE_WORKBENCHES[0],
    [activeWorkbench]
  );
  const activeSubViews = activeWorkbenchMeta?.views || [];

  const switchWorkbench = (workbench) => {
    setActiveWorkbench(workbench.key);
    if (!workbench.views.some(view => view.key === activeView)) {
      setActiveView(workbench.defaultView);
    }
  };

  const handleDinsarRunQueued = taskId => {
    onTaskStart?.(taskId, 'D-InSAR 任务已入队，等待处理...');
  };

  const handleDinsarProductQueued = taskId => {
    onTaskStart?.(taskId, 'D-InSAR 产物任务已入队，等待处理...');
  };

  const handleDinsarPrepareQueued = taskId => {
    onTaskStart?.(taskId, 'D-InSAR生产准备任务已入队，正在处理...', {
      taskType: 'COPY_DATA',
      nonBlocking: true,
    });
  };

  const handleSbasProductQueued = taskId => {
    onTaskStart?.(taskId, 'SBAS-InSAR result catalog task queued.', {
      taskType: 'REBUILD_SBAS_INSAR_CATALOG',
      nonBlocking: true,
    });
  };

  return (
    <div style={shellStyle}>
      <div style={heroStyle}>
        <section style={{ ...heroCardStyle, padding: '22px 24px' }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', color: '#1d4ed8', textTransform: 'uppercase' }}>
            Production Management
          </div>
          <h2 style={{ margin: '10px 0 12px', fontSize: 32, lineHeight: 1.1, color: '#0f172a' }}>生产管理</h2>
          <p style={{ margin: 0, maxWidth: 900, fontSize: 14, lineHeight: 1.8, color: '#475569' }}>
            这里统一承载 D-InSAR 配对、批次、生产准备、运行和产物管理，以及 Gamma SBAS-InSAR 生产链。
            陆探与哨兵源数据按压缩包登记，生产时再解包到本机 Task_Pool；高分三只登记外部 SARscape 服务器复制回来的 _geo 结果。
          </p>
        </section>

        <section
          style={{
            ...heroCardStyle,
            padding: '18px',
            display: 'grid',
            gap: 12,
            alignContent: 'start',
          }}
        >
          <div style={summaryCardStyle}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>主生产链</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>D-InSAR / SBAS</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              D-InSAR 使用配对批次驱动；SBAS 使用 Gamma IPTA 工作流驱动。PS/旧时序入口不再作为主流程展示。
            </div>
          </div>
          <div style={summaryCardStyle}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>运行边界</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>本机 Task_Pool</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              源压缩包先登记元数据，生产需要时再按需解包；D-InSAR/SBAS 不走 UNC。
            </div>
          </div>
          <div style={summaryCardStyle}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>结果管理</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>产物 catalog</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              生产结果进入 D-InSAR、SBAS 或 GF3 数据目录，后续分析从结果 catalog 读取。
            </div>
          </div>
        </section>
      </div>

      <section
        style={{
          ...heroCardStyle,
          padding: '14px',
          marginBottom: 18,
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {PRODUCTION_WORKSPACE_WORKBENCHES.map(workbench => {
            const isActive = workbench.key === activeWorkbench;
            return (
              <button
                key={workbench.key}
                type="button"
                onClick={() => switchWorkbench(workbench)}
                style={{
                  textAlign: 'left',
                  padding: '16px 18px',
                  borderRadius: 10,
                  border: `1px solid ${isActive ? '#93c5fd' : '#d7e0eb'}`,
                  background: isActive
                    ? '#eff6ff'
                    : 'rgba(255, 255, 255, 0.88)',
                  boxShadow: isActive ? '0 10px 24px rgba(37, 99, 235, 0.12)' : 'none',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                  <strong style={{ fontSize: 16, color: '#0f172a' }}>{workbench.label}</strong>
                  <span
                    style={{
                      padding: '4px 8px',
                      borderRadius: 8,
                      fontSize: 11,
                      fontWeight: 700,
                      color: isActive ? '#1d4ed8' : '#64748b',
                      background: isActive ? '#dbeafe' : '#f1f5f9',
                    }}
                  >
                    {isActive ? '当前视图' : '切换'}
                  </span>
                </div>
                <div style={{ fontSize: 12, lineHeight: 1.7, color: '#475569' }}>{workbench.description}</div>
              </button>
            );
          })}
        </div>
      </section>

      <section
        style={{
          ...heroCardStyle,
          padding: '12px',
          marginBottom: 18,
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
          {activeSubViews.map(view => {
            const isActive = view.key === activeView;
            return (
              <button
                key={view.key}
                type="button"
                onClick={() => setActiveView(view.key)}
                style={{
                  textAlign: 'left',
                  padding: '12px 14px',
                  borderRadius: 8,
                  border: `1px solid ${isActive ? '#2563eb' : '#d7e0eb'}`,
                  background: isActive ? '#ffffff' : '#f8fafc',
                  cursor: 'pointer',
                }}
              >
                <strong style={{ display: 'block', marginBottom: 6, fontSize: 14, color: isActive ? '#1d4ed8' : '#0f172a' }}>
                  {view.label}
                </strong>
                <span style={{ display: 'block', fontSize: 12, lineHeight: 1.6, color: '#475569' }}>
                  {view.description}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <div style={{ marginBottom: 12, fontSize: 12, color: '#64748b' }}>
          {activeWorkbenchMeta?.label} / {activeViewMeta.label}
        </div>
        <Suspense fallback={<PanelLoadingBody message={`正在加载 ${activeViewMeta.label}...`} />}>
          {SENSOR_PRODUCTION_PLACEHOLDERS[activeView] && (
            <SensorProductionPlaceholder viewKey={activeView} />
          )}
          {activeView === 'dinsar_pairing' && (
            <LazyPairPlanningPanel
              foundPairs={foundPairs}
              selectedPairsCount={selectedPairsCount}
              isLoading={isLoading}
              isReadOnlyUser={readOnly}
              hasEnoughRadarScenesForPlanning={hasEnoughRadarScenesForPlanning}
              onOpenPairingModal={pairingPanel.onOpenPairingModal}
              hasRadarSearched={hasRadarSearched}
              onRefreshRadarSearch={pairingPanel.onRefreshRadarSearch}
              onSearchAll={radarPanel.onSearchAll}
              onRefreshDinsar={pairingPanel.onRefreshDinsar}
              language={language}
            />
          )}
          {activeView === 'dinsar_pairs' && (
            <div style={{ display: 'grid', gap: 16 }}>
              <LazyPairsListPanel
                onVisualizePair={pairsPanel.onVisualizePair}
                onTogglePairVisibility={pairsPanel.onTogglePairVisibility}
                onCreateDinsarBatch={pairsPanel.onCreateDinsarBatch}
              />
              <LazyBatchPanel />
            </div>
          )}
          {activeView === 'dinsar_prepare' && (
            <LazyDataCopierPanel
              apiEndpoint={apiEndpoint}
              readOnly={readOnly}
              onJobQueued={handleDinsarPrepareQueued}
            />
          )}
          {activeView === 'dinsar_runs' && (
            <LazyDinsarProductionPanel
              readOnly={readOnly}
              onJobQueued={handleDinsarRunQueued}
            />
          )}
          {['sbas_insar_planning', 'sbas_insar_batches', 'sbas_insar_prepare', 'sbas_insar_runs'].includes(activeView) && (
            <LazySbasInsarProductionPanel
              readOnly={readOnly}
              onTaskStart={onTaskStart}
              initialFocus={{
                sbas_insar_planning: 'planning',
                sbas_insar_batches: 'batches',
                sbas_insar_prepare: 'prepare',
                sbas_insar_runs: 'runs',
              }[activeView]}
            />
          )}
          {activeView === 'sbas_insar_products' && (
            <LazySbasInsarProductsPanel
              readOnly={readOnly}
              onJobQueued={handleSbasProductQueued}
            />
          )}
          {activeView === 'dinsar_products' && (
            <LazyDinsarProductsPanel
              readOnly={readOnly}
              onJobQueued={handleDinsarProductQueued}
            />
          )}
        </Suspense>
      </section>
    </div>
  );
}
