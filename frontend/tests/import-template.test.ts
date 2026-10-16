// Юнит-тесты шаблона «Таблица КАМов» мастера импорта: CSV для Excel (BOM, «;»,
// экранирование), обязательные колонки совпадают с обязательными полями сервера
// (backend/app/modules/import_export/mapping.py KAM_CATALOG_FIELDS).
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  ENTITY_OPTIONS,
  entityGuide,
  parseCsv,
  registryPathFor,
  templateCsv,
} from '../src/pages/import/importModel.ts';

describe('Таблица КАМов в мастере импорта', () => {
  it('тип есть в списке и ведёт в реестр договоров', () => {
    const option = ENTITY_OPTIONS.find((o) => o.value === 'kam_catalog');
    assert.ok(option, 'kam_catalog отсутствует в ENTITY_OPTIONS');
    assert.equal(registryPathFor('kam_catalog'), '/registry/contracts');
  });

  it('памятка помечает обязательными вуз, продукт и номер договора', () => {
    const guide = entityGuide('kam_catalog');
    assert.ok(guide);
    const required = guide.columns.filter((c) => c.required).map((c) => c.name);
    assert.deepEqual(required, ['Вуз', 'ИТ-продукт (ПО)', 'Номер договора']);
    for (const row of guide.example) assert.equal(row.length, guide.columns.length);
  });

  it('у остальных типов памятки нет', () => {
    assert.equal(entityGuide('universities'), null);
  });

  it('шаблон — CSV с BOM и «;», кавычки и разделители экранируются', () => {
    const guide = entityGuide('kam_catalog');
    assert.ok(guide);
    const csv = templateCsv(guide);
    assert.ok(csv.startsWith('﻿'), 'нет BOM для Excel');
    const rows = parseCsv(csv, 10);
    assert.deepEqual(rows[0], guide.columns.map((c) => c.name));
    assert.deepEqual(rows[1], guide.example[0]);
    assert.equal(rows.length, 1 + guide.example.length);
    // «ООО «Пример Софт»» без «;» и кавычек-ASCII — в кавычки не берётся
    assert.ok(csv.includes(';ООО «Пример Софт»;'));
  });

  it('ячейка с «;» и кавычками берётся в кавычки', () => {
    const csv = templateCsv({
      summary: '',
      templateName: 't.csv',
      columns: [{ name: 'A', hint: '' }, { name: 'B', hint: '' }],
      example: [['Иванов И.И.; Петров П.П.', 'сказал "да"']],
    });
    const rows = parseCsv(csv, 10);
    assert.deepEqual(rows[1], ['Иванов И.И.; Петров П.П.', 'сказал "да"']);
  });
});
