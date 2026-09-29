import { Toaster as Sonner, type ToasterProps } from 'sonner';
import type { ReactElement } from 'react';

/**
 * Toaster sonner (§3.11): внизу справа, richColors выключен — цвета из токенов.
 * Обёртки toastSuccess/toastError — shared/lib/toast.ts.
 */
function Toaster(props: ToasterProps): ReactElement {
  return (
    <Sonner
      position="bottom-right"
      className="toaster group"
      style={
        {
          '--normal-bg': 'var(--popover)',
          '--normal-text': 'var(--foreground)',
          '--normal-border': 'var(--border)',
          '--success-bg': 'var(--status-success-tint)',
          '--success-text': 'var(--status-success-deep)',
          '--success-border': 'var(--status-success-tint)',
          '--error-bg': 'var(--status-danger-tint)',
          '--error-text': 'var(--status-danger-deep)',
          '--error-border': 'var(--status-danger-tint)',
        } as React.CSSProperties
      }
      toastOptions={{
        style: { boxShadow: 'var(--shadow-overlay)' },
      }}
      {...props}
    />
  );
}

export { Toaster };
