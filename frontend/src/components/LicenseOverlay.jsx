export default function LicenseOverlay({
  licenseLoading,
  licenseStatus,
  isAdmin,
  licenseFileRef,
  onUploadFile,
  onRefreshStatus,
  licenseFileName,
  licenseUploadStatus,
}) {
  if (!licenseLoading && licenseStatus?.ok) {
    return null;
  }

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      width: '100%',
      height: '100%',
      background: 'rgba(15, 23, 42, 0.82)',
      zIndex: 2000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <div style={{
        width: 'min(560px, 90%)',
        background: '#ffffff',
        borderRadius: '12px',
        padding: '24px',
        boxShadow: '0 20px 60px rgba(15, 23, 42, 0.35)',
      }}>
        <h3 style={{ marginTop: 0 }}>
          {licenseLoading ? '正在验证授权...' : '系统未授权'}
        </h3>
        {!licenseLoading && (
          <>
            <p style={{ color: '#475569', marginBottom: '12px' }}>
              失败原因：{licenseStatus?.reason || '授权无效或已过期，请联系管理员。'}
            </p>
            {isAdmin ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
                <input
                  type="file"
                  ref={licenseFileRef}
                  accept=".lic"
                  onChange={(e) => onUploadFile(e.target.files?.[0])}
                  style={{ display: 'none' }}
                />
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <button
                    className="primary-btn"
                    onClick={() => licenseFileRef.current && licenseFileRef.current.click()}
                    style={{ padding: '8px 14px' }}
                  >
                    选择授权文件
                  </button>
                  <button
                    className="secondary-btn"
                    onClick={onRefreshStatus}
                    style={{ padding: '8px 14px' }}
                  >
                    刷新授权状态
                  </button>
                  <span style={{ fontSize: '0.85em', color: '#64748b' }}>
                    {licenseFileName ? `已选择: ${licenseFileName}` : '未选择文件'}
                  </span>
                </div>
                {licenseUploadStatus?.message && (
                  <div style={{
                    fontSize: '0.85em',
                    color:
                      licenseUploadStatus.type === 'error'
                        ? '#dc2626'
                        : licenseUploadStatus.type === 'success'
                          ? '#16a34a'
                          : '#475569',
                  }}>
                    {licenseUploadStatus.message}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ marginBottom: '12px', color: '#b45309', background: '#fffbeb', border: '1px solid #fcd34d', padding: '8px 10px', borderRadius: '6px' }}>
                当前账号无上传授权权限，请联系管理员处理授权文件。
              </div>
            )}
            <div style={{ marginTop: '10px', background: '#f8fafc', padding: '10px 12px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
              <div style={{ fontWeight: 600, marginBottom: '6px', color: '#334155' }}>授权使用说明</div>
              <ol style={{ margin: 0, paddingLeft: '18px', color: '#475569', fontSize: '0.85em' }}>
                <li>确认已在服务器 .env 中配置 LICENSE_SECRET 与 LICENSE_PUBLIC_KEY。</li>
                <li>上传有效的 .lic 授权文件（与当前机器指纹匹配）。</li>
                <li>点击“刷新授权状态”确认授权已生效。</li>
              </ol>
            </div>
          </>
        )}
        {licenseLoading && (
          <p style={{ color: '#64748b' }}>请稍候，正在与授权文件进行校验。</p>
        )}
      </div>
    </div>
  );
}
