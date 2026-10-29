import test from 'node:test';
import assert from 'node:assert/strict';
import {shotRequestText} from '../web/shot_request.js';

test('old notes and higher priority action remain visible in one editor', () => {
  assert.equal(shotRequestText(undefined, '@图1人物，男人递钥匙', '女人离座'), '@图1人物，男人递钥匙\n\n动作与运镜（优先）：女人离座');
});
test('cleared unified request does not revive hidden legacy instructions', () => {
  assert.equal(shotRequestText('', '旧人物说明', '旧动作'), '');
  assert.equal(shotRequestText('新动作', '旧人物说明', '旧动作'), '新动作');
});
test('one legacy field and duplicate fields need no extra headings', () => {
  assert.equal(shotRequestText(undefined, '人物走路', ''), '人物走路');
  assert.equal(shotRequestText(undefined, '', '人物走路'), '人物走路');
  assert.equal(shotRequestText(undefined, '人物走路', '人物走路'), '人物走路');
});
