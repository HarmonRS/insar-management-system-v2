import ReactMarkdown from 'react-markdown';

export default function AiReportModal({ report, onClose }) {
  if (!report) {
    return null;
  }

  return (
    <div className="modal-overlay visible ai-report-modal">
      <div className="modal-content report-content">
        <div className="report-header">
          <h3>{report.title}</h3>
          <button className="close-btn" onClick={onClose}>关闭报告</button>
        </div>
        <div className="report-body markdown-body">
          <ReactMarkdown>{report.content}</ReactMarkdown>
        </div>
        <div className="report-footer">
          <button onClick={onClose}>已阅并关闭</button>
        </div>
      </div>
    </div>
  );
}
