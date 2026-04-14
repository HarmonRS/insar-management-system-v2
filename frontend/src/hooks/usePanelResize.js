import { useCallback, useEffect } from 'react';
import { clamp } from '../utils/appUiHelpers';

export default function usePanelResize({
  isResizing,
  setIsResizing,
  leftPanelWidth,
  rightPanelWidth,
  setLeftPanelWidth,
  setRightPanelWidth,
  resizeStateRef,
}) {
  const startResize = useCallback((side, event) => {
    event.preventDefault();
    resizeStateRef.current = {
      side,
      startX: event.clientX,
      startLeft: leftPanelWidth,
      startRight: rightPanelWidth,
    };
    setIsResizing(true);
  }, [resizeStateRef, leftPanelWidth, rightPanelWidth, setIsResizing]);

  useEffect(() => {
    if (!isResizing) {
      return;
    }

    const handleMove = (event) => {
      const { side, startX, startLeft, startRight } = resizeStateRef.current;
      const delta = event.clientX - startX;

      if (side === 'left') {
        setLeftPanelWidth(clamp(startLeft + delta, 320, 620));
      } else if (side === 'right') {
        setRightPanelWidth(clamp(startRight - delta, 280, 560));
      }
    };

    const handleUp = () => {
      setIsResizing(false);
    };

    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [isResizing, resizeStateRef, setLeftPanelWidth, setRightPanelWidth, setIsResizing]);

  return {
    startResize,
  };
}
