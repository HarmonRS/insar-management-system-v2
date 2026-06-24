export function PanelLoadingBody({ message = '正在加载面板...' }) {
  return (
    <div style={{ padding: 16 }}>
      <p className="empty-state">{message}</p>
    </div>
  );
}

export function PanelLoadingPanel({ message = '正在加载面板...' }) {
  return (
    <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'auto' }}>
      <PanelLoadingBody message={message} />
    </div>
  );
}

export function ModalLoadingFallback({ message = '正在加载内容...' }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15, 23, 42, 0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 2000,
      }}
    >
      <div
        style={{
          background: '#fff',
          borderRadius: 8,
          border: '1px solid #e2e8f0',
          padding: '24px 28px',
          minWidth: 280,
          boxShadow: '0 16px 32px rgba(15, 23, 42, 0.16)',
        }}
      >
        <p className="empty-state" style={{ margin: 0 }}>{message}</p>
      </div>
    </div>
  );
}
