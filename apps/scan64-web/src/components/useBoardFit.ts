import { useLayoutEffect, type RefObject } from 'react';
import type { Api } from 'chessground/api';

/**
 * Fit a Chessground board to the height left in its `.board-workspace`
 * (`--board-available-height`, read by Board.css), and redraw it on resize.
 */
export function useBoardFit(element: RefObject<HTMLElement | null>, board: RefObject<Api | null>): void {
  useLayoutEffect(() => {
    const frame = element.current?.parentElement;
    if (!frame) return;
    const workspace = frame.closest<HTMLElement>('.board-workspace');
    const main = frame.closest('main');
    const updateAvailableHeight = () => {
      if (!workspace) return;
      // Use the unscrolled page position so playing does not resize the board as
      // the document scrolls. Header/status changes and shell reflows are measured.
      const top = workspace.getBoundingClientRect().top + window.scrollY;
      const styles = getComputedStyle(workspace);
      const clearance = parseFloat(styles.getPropertyValue('--shell-clearance')) || 0;
      const padding = parseFloat(styles.getPropertyValue('--page-padding')) || 0;
      workspace.style.setProperty('--board-available-height', `${window.innerHeight - top - clearance - padding}px`);
    };
    let redraw = 0;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      updateAvailableHeight();
      cancelAnimationFrame(redraw);
      redraw = requestAnimationFrame(() => board.current?.redrawAll());
    });
    observer?.observe(frame);
    if (workspace) observer?.observe(workspace);
    if (main) observer?.observe(main);
    updateAvailableHeight();
    window.addEventListener('resize', updateAvailableHeight);
    return () => {
      observer?.disconnect();
      cancelAnimationFrame(redraw);
      window.removeEventListener('resize', updateAvailableHeight);
      workspace?.style.removeProperty('--board-available-height');
    };
  }, [element, board]);
}
