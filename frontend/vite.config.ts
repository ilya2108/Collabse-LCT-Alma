import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

/**
 * Дев-сервер проксирует /api на локальный backend (deployment.md §5):
 * FastAPI слушает 8000, префикс /api/v1 совпадает — rewrite не нужен.
 * В проде статику раздаёт nginx (frontend/nginx.conf, зона deploy),
 * рантайм-конфиг приходит из /config.js (window.__ENV__).
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: false,
    // Ручное разбиение vendor-чанков намеренно не используем: manualChunks
    // легко образует цикл между чанками с порчей порядка инициализации.
    // Тяжёлые экраны (ECharts-дашборд, React Flow-конструктор, мастер импорта)
    // подключаются через dynamic import — Vite режет их в отдельные чанки
    // автоматически и безопасно (redesign.md §8.3). Основной чанк ~1.36 МБ
    // (≈410 КБ gzip, AntD удалён) кэшируется nginx'ом с immutable-заголовком.
    chunkSizeWarningLimit: 1500,
  },
});
