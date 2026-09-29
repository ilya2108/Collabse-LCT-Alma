import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// Единственная точка входа стилей (redesign.md §1.1): Tailwind v4 + токены +
// Inter Variable self-hosted (@fontsource-variable/inter, woff2 в бандле — закрытый контур).
import './styles/globals.css';
import { App } from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Не найден корневой элемент #root');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
