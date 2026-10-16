// ESLint: только правила хуков React (остальное проверяет tsc в строгом режиме).
// rules-of-hooks — ошибка; exhaustive-deps — предупреждение: порог `--max-warnings`
// в `npm run lint` равен текущему числу предупреждений и запрещает только новые.
// Неиспользуемые `eslint-disable` тоже считаются предупреждениями.
import tsParser from '@typescript-eslint/parser';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'warn',
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
];
