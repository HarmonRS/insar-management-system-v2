import { useEffect, useMemo, useRef, useState } from 'react';

const DEFAULT_OVERSCAN = 6;

export default function VirtualizedList({
    items,
    itemHeight,
    renderItem,
    getKey,
    overscan = DEFAULT_OVERSCAN,
    viewportClassName = '',
    contentClassName = '',
    viewportStyle,
}) {
    const viewportRef = useRef(null);
    const [scrollTop, setScrollTop] = useState(0);
    const [viewportHeight, setViewportHeight] = useState(0);

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport) return undefined;

        const updateViewportHeight = () => {
            setViewportHeight(viewport.clientHeight || 0);
        };

        updateViewportHeight();

        if (typeof ResizeObserver === 'undefined') {
            window.addEventListener('resize', updateViewportHeight);
            return () => window.removeEventListener('resize', updateViewportHeight);
        }

        const observer = new ResizeObserver(updateViewportHeight);
        observer.observe(viewport);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport) return;
        if (viewport.scrollTop > 0 && items.length * itemHeight <= viewport.clientHeight) {
            viewport.scrollTop = 0;
            setScrollTop(0);
        }
    }, [items.length, itemHeight]);

    const totalHeight = items.length * itemHeight;
    const visibleRange = useMemo(() => {
        const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
        const endIndex = Math.min(
            items.length,
            Math.ceil((scrollTop + Math.max(viewportHeight, itemHeight)) / itemHeight) + overscan
        );

        return {
            startIndex,
            endIndex,
        };
    }, [itemHeight, items.length, overscan, scrollTop, viewportHeight]);

    const visibleItems = useMemo(
        () => items.slice(visibleRange.startIndex, visibleRange.endIndex),
        [items, visibleRange.endIndex, visibleRange.startIndex]
    );

    return (
        <div
            ref={viewportRef}
            className={`virtual-list-viewport ${viewportClassName}`.trim()}
            onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
            style={viewportStyle}
        >
            <div className="virtual-list-spacer" style={{ height: totalHeight }}>
                <ul
                    className={`data-list virtual-list-content ${contentClassName}`.trim()}
                    style={{ transform: `translateY(${visibleRange.startIndex * itemHeight}px)` }}
                >
                    {visibleItems.map((item, index) => {
                        const absoluteIndex = visibleRange.startIndex + index;
                        return renderItem(item, absoluteIndex, getKey(item, absoluteIndex));
                    })}
                </ul>
            </div>
        </div>
    );
}
