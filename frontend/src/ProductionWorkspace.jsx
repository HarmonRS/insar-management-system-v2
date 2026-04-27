import { Suspense, lazy, useEffect, useMemo, useState } from 'react';

import {
  PRODUCTION_WORKSPACE_ENTRY_TO_VIEW,
  PRODUCTION_WORKSPACE_TAB,
  PRODUCTION_WORKSPACE_VIEWS,
} from './config/appConstants';
import { PanelLoadingBody } from './components/app/AppLoadingFallbacks';

const LazyDinsarProductionPanel = lazy(() => import('./DinsarProductionPanel'));
const LazyTimeseriesProductionPanel = lazy(() => import('./TimeseriesProductionPanel'));
const LazyDinsarProductsPanel = lazy(() => import('./DinsarProductsPanel'));
const LazyPsinsarCatalogPanel = lazy(() => import('./components/PsinsarCatalogPanel'));

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

function resolveView(entry) {
  return PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[entry] || PRODUCTION_WORKSPACE_ENTRY_TO_VIEW[PRODUCTION_WORKSPACE_TAB];
}

export default function ProductionWorkspace({
  activeEntry = PRODUCTION_WORKSPACE_TAB,
  readOnly = false,
  onTaskStart,
}) {
  const [activeView, setActiveView] = useState(() => resolveView(activeEntry));

  useEffect(() => {
    setActiveView(resolveView(activeEntry));
  }, [activeEntry]);

  const activeViewMeta = useMemo(
    () => PRODUCTION_WORKSPACE_VIEWS.find(view => view.key === activeView) || PRODUCTION_WORKSPACE_VIEWS[0],
    [activeView]
  );

  const handleDinsarRunQueued = taskId => {
    onTaskStart?.(taskId, 'D-InSAR 任务已入队，等待处理...');
  };

  const handleTimeseriesRunQueued = taskId => {
    onTaskStart?.(taskId, '时序InSAR 运行已入队，当前默认执行 SBAS 流程...');
  };

  const handleDinsarProductQueued = taskId => {
    onTaskStart?.(taskId, 'D-InSAR 产物任务已入队，等待处理...');
  };

  const handleTimeseriesProductQueued = taskId => {
    onTaskStart?.(taskId, '时序InSAR 产物目录任务已入队，等待处理...');
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
            这里统一承载 D-InSAR 与时序InSAR的运行和产物工作台。当前时序入口默认接入 SBAS 实现，
            后续可在同一界面继续扩展 PS-InSAR、SBAS-InSAR 以及更多 WSL2 引擎实例。
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
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>统一入口</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>运行与产物同域编排</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              生产运行、目录重建、产物编目全部收口到同一顶级工作区。
            </div>
          </div>
          <div style={summaryCardStyle}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>时序当前实现</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>SBAS 默认接入</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              现阶段实际运行链路为 SBAS，界面命名已统一为时序InSAR。
            </div>
          </div>
          <div style={summaryCardStyle}>
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>引擎预留</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>ISCE / Gamma 可扩</div>
            <div style={{ fontSize: 12, lineHeight: 1.6, color: '#475569', marginTop: 4 }}>
              现有 WSL2 嵌入保持不变，后续增加 Gamma 时可直接挂入当前工作台。
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
          {PRODUCTION_WORKSPACE_VIEWS.map(view => {
            const isActive = view.key === activeView;
            return (
              <button
                key={view.key}
                type="button"
                onClick={() => setActiveView(view.key)}
                style={{
                  textAlign: 'left',
                  padding: '14px 16px',
                  borderRadius: 18,
                  border: `1px solid ${isActive ? '#93c5fd' : '#d7e0eb'}`,
                  background: isActive
                    ? 'linear-gradient(135deg, #eff6ff 0%, #f8fbff 100%)'
                    : 'rgba(255, 255, 255, 0.88)',
                  boxShadow: isActive ? '0 10px 24px rgba(37, 99, 235, 0.12)' : 'none',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                  <strong style={{ fontSize: 15, color: '#0f172a' }}>{view.label}</strong>
                  <span
                    style={{
                      padding: '4px 8px',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 700,
                      color: isActive ? '#1d4ed8' : '#64748b',
                      background: isActive ? '#dbeafe' : '#f1f5f9',
                    }}
                  >
                    {isActive ? '当前视图' : '切换'}
                  </span>
                </div>
                <div style={{ fontSize: 12, lineHeight: 1.7, color: '#475569' }}>{view.description}</div>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <div style={{ marginBottom: 12, fontSize: 12, color: '#64748b' }}>{activeViewMeta.label}</div>
        <Suspense fallback={<PanelLoadingBody message={`正在加载 ${activeViewMeta.label}...`} />}>
          {activeView === 'dinsar_runs' && (
            <LazyDinsarProductionPanel
              readOnly={readOnly}
              onJobQueued={handleDinsarRunQueued}
            />
          )}
          {activeView === 'timeseries_runs' && (
            <LazyTimeseriesProductionPanel
              readOnly={readOnly}
              onJobQueued={handleTimeseriesRunQueued}
            />
          )}
          {activeView === 'dinsar_products' && (
            <LazyDinsarProductsPanel
              readOnly={readOnly}
              onJobQueued={handleDinsarProductQueued}
            />
          )}
          {activeView === 'timeseries_products' && (
            <LazyPsinsarCatalogPanel
              readOnly={readOnly}
              showActions
              onTaskQueued={handleTimeseriesProductQueued}
            />
          )}
        </Suspense>
      </section>
    </div>
  );
}
