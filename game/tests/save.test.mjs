import assert from 'node:assert/strict';
import test from 'node:test';
import {loadPlayer, storePlayer} from '../src/save.ts';

const values = new Map();
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
};

test('player save is rejected when the scientific source hash changes', () => {
  storePlayer({
    schema: 'p1-player-save-v1',
    runId: 'run-1',
    sourceHash: 'source-a',
    latitudeDeg: 12,
    longitudeDeg: 34,
    heightM: 2,
    camera: 'third',
    savedAt: '2026-11-20T00:00:00Z',
  });
  assert.equal(loadPlayer('run-1', 'source-b'), null);
  assert.equal(loadPlayer('run-1', 'source-a').latitudeDeg, 12);
});
