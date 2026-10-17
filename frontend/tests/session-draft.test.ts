// Юнит-тесты черновиков в sessionStorage (ФТ13): чтение, очистка, отказ хранилища.
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  clearDrafts,
  COMMENT_DRAFT_PREFIX,
  readDraft,
  writeDraft,
} from '../src/shared/lib/sessionDraft.ts';

function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
}

const broken = {
  getItem: () => {
    throw new Error('SecurityError');
  },
  setItem: () => {
    throw new Error('QuotaExceededError');
  },
  removeItem: () => {
    throw new Error('SecurityError');
  },
};

describe('sessionDraft', () => {
  it('черновик по ключу заявки сохраняется и читается', () => {
    const storage = memoryStorage();
    writeDraft('alma:comment-draft:r1', 'Созвонились с проректором', storage);
    assert.equal(readDraft('alma:comment-draft:r1', storage), 'Созвонились с проректором');
    assert.equal(readDraft('alma:comment-draft:r2', storage), '');
  });

  it('пустой черновик (после отправки) удаляет ключ', () => {
    const storage = memoryStorage();
    writeDraft('alma:comment-draft:r1', 'текст', storage);
    writeDraft('alma:comment-draft:r1', '', storage);
    assert.equal(storage.length, 0);
  });

  it('без хранилища или при его ошибке — пустой черновик, без исключений', () => {
    assert.equal(readDraft('k', null), '');
    assert.doesNotThrow(() => writeDraft('k', 'v', null));
    assert.equal(readDraft('k', broken), '');
    assert.doesNotThrow(() => writeDraft('k', 'v', broken));
    assert.doesNotThrow(() => writeDraft('k', '', broken));
  });

  it('выход из системы стирает все черновики комментариев и только их', () => {
    const storage = memoryStorage();
    writeDraft(`${COMMENT_DRAFT_PREFIX}u1:r1`, 'черновик 1', storage);
    writeDraft(`${COMMENT_DRAFT_PREFIX}u1:r2`, 'черновик 2', storage);
    writeDraft('alma:other', 'не черновик', storage);
    clearDrafts(COMMENT_DRAFT_PREFIX, storage);
    assert.equal(storage.length, 1);
    assert.equal(readDraft('alma:other', storage), 'не черновик');
    assert.doesNotThrow(() => clearDrafts(COMMENT_DRAFT_PREFIX, null));
  });
});
