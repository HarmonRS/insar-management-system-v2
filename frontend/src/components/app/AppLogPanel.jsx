import { useUiStore } from '../../store';
import { useI18n } from '../../i18n/I18nContext';

export default function AppLogPanel({ width }) {
    const logs = useUiStore((state) => state.logs);
    const { t } = useI18n();

    return (
        <aside className="panel right-panel" style={{ width }}>
            <header>
                <h3>日志</h3>
            </header>
            <div className="panel-content log-entries">
                {logs.length === 0 ? (
                    <p className="empty-state">暂无日志。</p>
                ) : (
                    logs.map((log, index) => (
                        <div key={index} className="log-entry">
                            <span className="log-time">[{log.time}]</span>
                            <span className="log-message" data-log-type={log.type}>{t(log.message)}</span>
                        </div>
                    ))
                )}
            </div>
        </aside>
    );
}
