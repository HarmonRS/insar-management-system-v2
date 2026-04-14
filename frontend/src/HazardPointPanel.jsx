import React, { useState, useEffect, useRef } from 'react';
import apiClient from './api/client';

const HazardPointPanel = ({ onPointClick, onToggleVisibility, isVisible, onScanComplete, onTaskStart, points: externalPoints, readOnly = false }) => {
    const [points, setPoints] = useState(Array.isArray(externalPoints) ? externalPoints : []);
    const [searchTerm, setSearchTerm] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [message, setMessage] = useState('');
    const fetchPointsRef = useRef(null);

    const fetchPoints = async () => {
        setIsLoading(true);
        try {
            const response = await apiClient.get('/hazard-points');
            setPoints(response.data);
        } catch (error) {
            console.error("获取灾害点失败:", error);
            setMessage("获取灾害点失败");
        } finally {
            setIsLoading(false);
        }
    };
    fetchPointsRef.current = fetchPoints;

    useEffect(() => {
        if (!Array.isArray(externalPoints)) {
            fetchPointsRef.current?.();
        }
    }, [externalPoints]);

    useEffect(() => {
        if (Array.isArray(externalPoints)) {
            setPoints(externalPoints);
        }
    }, [externalPoints]);

    const handleScan = async () => {
        if (readOnly) {
            setMessage("当前账号为只读模式，无法同步灾害点。");
            return;
        }
        setIsLoading(true);
        setMessage("正在发起灾害点同步任务...");
        try {
            const response = await apiClient.post('/hazard-points/scan');
            const { task_id, message: msg } = response.data;
            setMessage(msg);
            if (onScanComplete) onScanComplete();
            if (onTaskStart) onTaskStart(task_id, "正在同步灾害点数据...");
        } catch (error) {
            setMessage(`同步失败: ${error.response?.data?.detail || error.message}`);
        } finally {
            setIsLoading(false);
        }
    };

    const filteredPoints = points.filter(p => {
        const tybh = String(p.tybh || '');
        const hazardName = String(p.hazard_name || '');
        const city = String(p.city || '');
        const county = String(p.county || '');
        return (
            tybh.includes(searchTerm) ||
            hazardName.includes(searchTerm) ||
            city.includes(searchTerm) ||
            county.includes(searchTerm)
        );
    });

    return (
        <div className="hazard-point-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ padding: '15px', borderBottom: '1px solid #eee' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                    <h4 style={{ margin: 0 }}>地质灾害点监测</h4>
                    <button 
                        className="secondary-btn" 
                        onClick={() => onToggleVisibility(!isVisible)}
                        style={{ fontSize: '0.8em', padding: '4px 8px' }}
                    >
                        {isVisible ? '隐藏全部' : '显示全部'}
                    </button>
                </div>
                
                <button 
                    className="primary-btn" 
                    onClick={handleScan} 
                    disabled={isLoading || readOnly}
                    style={{ width: '100%', marginBottom: '10px' }}
                >
                    {isLoading ? '同步中...' : '同步 Shapefile 数据'}
                </button>

                <input 
                    type="text" 
                    placeholder="搜索编号、名称、市县..." 
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
                />
            </div>

            <div className="panel-content" style={{ flex: 1, overflowY: 'auto' }}>
                {filteredPoints.length === 0 ? (
                    <p className="empty-state">未找到匹配的灾害点</p>
                ) : (
                    <ul className="data-list">
                        {filteredPoints.map(point => (
                            <li 
                                key={point.tybh} 
                                className="data-item"
                                onClick={() => onPointClick(point)}
                                style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px' }}
                            >
                                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                                    <span style={{ fontWeight: 'bold', color: '#2d3748' }}>{point.hazard_name}</span>
                                    <span style={{ fontSize: '0.8em', color: '#718096' }}>{point.tybh}</span>
                                </div>
                                <div style={{ fontSize: '0.85em', color: '#4a5568' }}>
                                    {point.hazard_type} | {point.city}-{point.county}
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {message && (
                <div style={{ padding: '10px', fontSize: '0.85em', background: '#ebf8ff', color: '#2b6cb0', borderTop: '1px solid #bee3f8' }}>
                    {message}
                </div>
            )}
        </div>
    );
};

export default HazardPointPanel;
