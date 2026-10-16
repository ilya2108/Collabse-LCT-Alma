// Юнит-тесты цвета этапа: текст бейджа ≥ 4.5:1 к тинту и к белому для любого hex,
// палитра сидов схем B2B/B2C — из STAGE_COLOR_PRESET (shared/config/tokens.ts).
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

import {
  STAGE_COLOR_NAMES,
  STAGE_COLOR_PRESET,
  STAGE_TEXT_MIN_CONTRAST,
  contrastRatio,
  stageText,
  stageTint,
} from '../src/shared/config/tokens.ts';

/** Старые цвета сидов (AntD): на стенде они живут до пересева — бейджи тоже должны читаться. */
const LEGACY_SEED_COLORS = [
  '#8c8c8c',
  '#13c2c2',
  '#1677ff',
  '#722ed1',
  '#fa8c16',
  '#52c41a',
  '#a0d911',
  '#237804',
  '#f5222d',
];
/** Произвольные цвета из API: светлые, насыщенные, короткая запись. */
const ARBITRARY = ['#FFFF00', '#00FFFF', '#FFC0CB', '#fff', '#000000', '#7F7F7F', '#0F0'];

const HEX = /^#[0-9A-F]{6}$/;

function seedColors(code: string): string[] {
  const url = new URL(`../../backend/app/modules/workflow/seeds/${code}.json`, import.meta.url);
  const spec = JSON.parse(readFileSync(url, 'utf-8')) as { stages: { color: string }[] };
  return spec.stages.map((stage) => stage.color.toUpperCase());
}

describe('stageText / stageTint', () => {
  for (const color of [...STAGE_COLOR_PRESET, ...LEGACY_SEED_COLORS, ...ARBITRARY]) {
    it(`${color}: текст ≥ 4.5:1 к тинту и к белому`, () => {
      const tint = stageTint(color);
      const text = stageText(color);
      assert.match(tint, HEX);
      assert.match(text, HEX);
      assert.ok(contrastRatio(text, tint) >= STAGE_TEXT_MIN_CONTRAST, `${text} на ${tint}`);
      assert.ok(contrastRatio(text, '#FFFFFF') >= STAGE_TEXT_MIN_CONTRAST);
    });
  }

  it('у пресета текст остаётся цветным, а не чёрным', () => {
    for (const color of STAGE_COLOR_PRESET) {
      assert.notEqual(stageText(color), '#000000', color);
    }
  });

  it('тинт — светлый фон: цвет этапа в нём на 10 %', () => {
    for (const color of STAGE_COLOR_PRESET) {
      assert.ok(contrastRatio('#FFFFFF', stageTint(color)) < 1.25, color);
    }
  });

  it('не-hex цвет — CSS color-mix как запасной путь', () => {
    assert.equal(stageTint('var(--primary)'), 'color-mix(in oklab, var(--primary) 10%, white)');
    assert.match(stageText('var(--primary)'), /^color-mix\(in oklab, var\(--primary\) 60%, black\)$/);
    assert.ok(Number.isNaN(contrastRatio('var(--x)', '#FFFFFF')));
  });

  it('у каждого пресета есть русское имя для aria-label', () => {
    for (const color of STAGE_COLOR_PRESET) assert.ok(STAGE_COLOR_NAMES[color], color);
  });
});

describe('палитра сидов схем', () => {
  for (const code of ['b2b', 'b2c']) {
    it(`${code}: цвета этапов — из STAGE_COLOR_PRESET, без повторов`, () => {
      const colors = seedColors(code);
      const preset = new Set<string>(STAGE_COLOR_PRESET);
      for (const color of colors) assert.ok(preset.has(color), `${code}: ${color} не из пресета`);
      assert.equal(new Set(colors).size, colors.length);
    });
  }
});
