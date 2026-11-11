import test from 'node:test';
import assert from 'node:assert/strict';
import {seedText} from '../web/progress.js';

test('seed display distinguishes pending reroll from the submitted task seed',()=>{
  assert.ok(seedText({reroll_pending:true,job:{seeds:{270:42}}}).includes('开始生成时更换种子'));
  assert.ok(seedText({job:{seeds:{270:9007199254740991}},seed_overrides:{270:13}}).includes('9007199254740991'));
  assert.ok(seedText({seed_overrides:{270:0}}).includes('0'));
  assert.equal(seedText({job:{status:'completed'}}),'');
});
