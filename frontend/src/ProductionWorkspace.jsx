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
const LazyLandsarLt1ProductionPanel = lazy(() => import('./LandsarLt1ProductionPanel'));
const LazyPairPlanningPanel = lazy(() => import('./panels/PairPlanningPanel'));
const LazyPairsListPanel = lazy(() => import('./panels/PairsListPanel'));
const LazyBatchPanel = lazy(() => import('./panels/BatchPanel'));
const LazyDataCopierPanel = lazy(() => import('./DataCopierPanel'));

const WORKFLOW_STEPS = [
  '数据准备',
  '配对/栈规划',
  '生产运行',
  '质量检查',
  '成果发布',
];

const SENSOR_PRODUCTION_PLACEHOLDERS = {
  sentinel1_production: {
    title: 'Sentinel-1 生产占位',
    note: '当前主要沉淀数据与精轨管理约束，SBAS 仅保留规划能力。',
    rows: [
      ['数据来源', 'ZIP/SAFE 本机 archive'],
      ['精轨策略', 'EOF 精轨本机管理'],
      ['准备方式', '按需解包到工作目录'],
      ['D-InSAR', '走统一生产任务队列'],
      ['SBAS', '保留序列规划能力'],
    ],
  },
  gf3_native_registration: {
    title: '高分三结果登记',
    note: 'GF3 由外部 SARscape 服务生产，本系统登记回传成果并生成预览。',
    rows: [
      ['生产方式', '外部 SARscape 服务'],
      ['落地路径', '本机登记 _geo 二进制'],
      ['预览生成', '转换 WebP 供地图使用'],
      ['精轨策略', '按外部生产结果留痕'],
      ['结果管理', '进入统一产品 catalog'],
    ],
  },
};

const shellStyle = {
  minHeight: '100%',
  background: '#f8fafc',
  color: '#0f172a',
};

const headerStyle = {
  padding: '18px 20px 14px',
  borderBottom: '1px solid #e2e8f0',
  background: '#ffffff',
};

const sectionStyle = {
  padding: '16px 20px 22px',
};

const compactPanelStyle = {
  border: '1px solid #e2e8f0',
  borderRadius: 8,
  background: '#ffffff',
};

const mutedTextStyle = {
  color: '#64748b',
  fontSize: 13,
  lineHeight: 1.6,
};

function resolveView(activeEntry) {
  return PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[activeEntry] || PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[PRODUCTION_WORKSPACE_TAB];
}

function resolveWorkbenchKey(viewKey) {
  const workbench = PRODUCTION_WORKSPACE_WORKBENCHES.find(item =>
    item.views.some(view => view.key === viewKey),
  );
  return workbench?.key || PRODUCTION_WORKSPACE_WORKBENCHES[0]?.key;
}

function buttonStyle(active) {
  return {
    border: `1px solid ${active ? '#2563eb' : '#cbd5e1'}`,
    background: active ? '#eff6ff' : '#ffffff',
    color: active ? '#1d4ed8' : '#334155',
    borderRadius: 6,
    padding: '7px 10px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    lineHeight: 1.3,
  };
}

function PlaceholderView({ config }) {
  if (!config) {
    return (
      <div style={{ ...compactPanelStyle, padding: 18 }}>
        <h3 style={{ margin: 0, fontSize: 16 }}>生产入口未配置</h3>
        <p style={{ ...mutedTextStyle, margin: '8px 0 0' }}>当前视图尚未接入生产面板。</p>
      </div>
    );
  }

  return (
    <div style={{ ...compactPanelStyle, padding: 18 }}>
      <h3 style={{ margin: 0, fontSize: 16 }}>{config.title}</h3>
      <p style={{ ...mutedTextStyle, margin: '8px 0 14px' }}>{config.note}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(96px, 140px) 1fr', gap: 0, borderTop: '1px solid #e2e8f0' }}>
        {config.rows.map(([label, value]) => (
          <div key={label} style={{ display: 'contents' }}>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid #e2e8f0', color: '#475569', background: '#f8fafc', fontSize: 13 }}>
              {label}
            </div>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid #e2e8f0', color: '#0f172a', fontSize: 13 }}>
              {value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ProductionWorkspace({
  activeEntry,
  readOnly,
  onTaskStart,
  apiEndpoint,
  language,
  foundPairs,
  selectedPairsCount,
  isLoading,
  hasEnoughRadarScenesForPlanning,
  hasRadarSearched,
  pairingPanel,
  radarPanel,
  pairsPanel,
}) {
  const initialView = resolveView(activeEntry);
  const [activeView, setActiveView] = useState(initialView);
  const [activeWorkbench, setActiveWorkbench] = useState(resolveWorkbenchKey(initialView));

  useEffect(() => {
    const nextView = resolveView(activeEntry);
    setActiveView(nextView);
    setActiveWorkbench(resolveWorkbenchKey(nextView));
  }, [activeEntry]);

  const currentWorkbench = useMemo(
    () => PRODUCTION_WORKSPACE_WORKBENCHES.find(item => item.key === activeWorkbench) || PRODUCTION_WORKSPACE_WORKBENCHES[0],
    [activeWorkbench],
  );
  const currentView = useMemo(
    () => PRODUCTION_WORKSPACE_VIEWS.find(view => view.key === activeView),
    [activeView],
  );

  const switchWorkbench = workbench => {
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
    onTaskStart?.(taskId, 'D-InSAR 生产准备任务已入队，正在处理...', {
      taskType: 'COPY_DATA',
      nonBlocking: true,
    });
  };

  const handleSbasProductQueued = taskId => {
    onTaskStart?.(taskId, 'SBAS-InSAR 结果 catalog 任务已入队。', {
      taskType: 'REBUILD_SBAS_INSAR_CATALOG',
      nonBlocking: true,
    });
  };

  const handleLt1ImageQueued = taskId => {
    onTaskStart?.(taskId, 'LT-1 地理编码 GeoTIFF 生产任务已入队。', {
      taskType: 'SAR_SCENE_PREPROCESS',
      nonBlocking: true,
    });
  };

  const renderContent = () => {
    if (activeView === 'dinsar_pairing') {
      return (
        <LazyPairPlanningPanel
          foundPairs={foundPairs}
          selectedPairsCount={selectedPairsCount}
          isLoading={isLoading}
          isReadOnlyUser={readOnly}
          hasEnoughRadarScenesForPlanning={hasEnoughRadarScenesForPlanning}
          onOpenPairingModal={pairingPanel?.onOpenPairingModal}
          hasRadarSearched={hasRadarSearched}
          onRefreshRadarSearch={pairingPanel?.onRefreshRadarSearch}
          onSearchAll={radarPanel?.onSearchAll}
          onRefreshDinsar={pairingPanel?.onRefreshDinsar}
          language={language}
        />
      );
    }

    if (activeView === 'dinsar_pairs') {
      return (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.1fr) minmax(360px, 0.9fr)', gap: 14 }}>
          <LazyPairsListPanel
            onVisualizePair={pairsPanel?.onVisualizePair}
            onTogglePairVisibility={pairsPanel?.onTogglePairVisibility}
            onCreateDinsarBatch={pairsPanel?.onCreateDinsarBatch}
          />
          <LazyBatchPanel />
        </div>
      );
    }

    if (activeView === 'dinsar_prepare') {
      return (
        <LazyDataCopierPanel
          apiEndpoint={apiEndpoint}
          readOnly={readOnly}
          onJobQueued={handleDinsarPrepareQueued}
        />
      );
    }

    if (activeView === 'dinsar_runs') {
      return <LazyDinsarProductionPanel readOnly={readOnly} onJobQueued={handleDinsarRunQueued} />;
    }

    if (activeView === 'dinsar_products') {
      return <LazyDinsarProductsPanel readOnly={readOnly} onJobQueued={handleDinsarProductQueued} />;
    }

    if (['sbas_insar_planning', 'sbas_insar_batches', 'sbas_insar_prepare', 'sbas_insar_runs'].includes(activeView)) {
      const focusMap = {
        sbas_insar_planning: 'planning',
        sbas_insar_batches: 'batches',
        sbas_insar_prepare: 'prepare',
        sbas_insar_runs: 'runs',
      };
      return (
        <LazySbasInsarProductionPanel
          readOnly={readOnly}
          onTaskStart={onTaskStart}
          initialFocus={focusMap[activeView]}
        />
      );
    }

    if (activeView === 'sbas_insar_products') {
      return <LazySbasInsarProductsPanel readOnly={readOnly} onJobQueued={handleSbasProductQueued} />;
    }

    if (activeView === 'lt1_production') {
      return <LazyLandsarLt1ProductionPanel readOnly={readOnly} onJobQueued={handleLt1ImageQueued} />;
    }

    return <PlaceholderView config={SENSOR_PRODUCTION_PLACEHOLDERS[activeView]} />;
  };

  return (
    <div className="production-workspace-shell" style={shellStyle}>
      <div style={headerStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, color: '#475569', marginBottom: 4 }}>生产工作台</div>
            <h2 style={{ margin: 0, fontSize: 22, lineHeight: 1.25, letterSpacing: 0 }}>InSAR 生产管理</h2>
            <p style={{ ...mutedTextStyle, margin: '8px 0 0', maxWidth: 720 }}>
              面向科研工程生产的任务编排入口，统一组织数据准备、规划、运行、质量检查与成果发布。
            </p>
          </div>
          {readOnly && (
            <div style={{ border: '1px solid #f59e0b', color: '#92400e', background: '#fffbeb', borderRadius: 6, padding: '8px 10px', fontSize: 13 }}>
              当前为只读账号，生产提交操作已禁用。
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
          {WORKFLOW_STEPS.map((step, index) => (
            <div
              key={step}
              style={{
                border: '1px solid #e2e8f0',
                background: '#f8fafc',
                borderRadius: 6,
                padding: '6px 9px',
                color: '#334155',
                fontSize: 12,
                lineHeight: 1.2,
              }}
            >
              {index + 1}. {step}
            </div>
          ))}
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) minmax(0, 1fr)', gap: 14, alignItems: 'start' }}>
          <aside style={{ ...compactPanelStyle, padding: 12 }}>
            <div style={{ color: '#475569', fontSize: 13, marginBottom: 8 }}>工作流</div>
            <div style={{ display: 'grid', gap: 8 }}>
              {PRODUCTION_WORKSPACE_WORKBENCHES.map(workbench => (
                <button
                  key={workbench.key}
                  type="button"
                  onClick={() => switchWorkbench(workbench)}
                  style={{ ...buttonStyle(activeWorkbench === workbench.key), textAlign: 'left' }}
                >
                  <span style={{ display: 'block' }}>{workbench.label}</span>
                  <span style={{ display: 'block', marginTop: 4, color: activeWorkbench === workbench.key ? '#2563eb' : '#64748b', fontWeight: 400 }}>
                    {workbench.description}
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <main style={{ display: 'grid', gap: 14, minWidth: 0 }}>
            <div style={{ ...compactPanelStyle, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <div>
                  <div style={{ color: '#475569', fontSize: 13 }}>{currentWorkbench?.label}</div>
                  <h3 style={{ margin: '3px 0 0', fontSize: 18 }}>{currentView?.label || '生产视图'}</h3>
                </div>
                <div style={{ ...mutedTextStyle, maxWidth: 560 }}>{currentView?.description}</div>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                {currentWorkbench?.views.map(view => (
                  <button
                    key={view.key}
                    type="button"
                    onClick={() => setActiveView(view.key)}
                    style={buttonStyle(activeView === view.key)}
                  >
                    {view.label}
                  </button>
                ))}
              </div>
            </div>

            <Suspense fallback={<PanelLoadingBody message="正在加载生产面板..." />}>
              {renderContent()}
            </Suspense>
          </main>
        </div>
      </div>
    </div>
  );
}
