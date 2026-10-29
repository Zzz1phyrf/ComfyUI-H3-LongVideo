import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('the final audio preview keeps source duration precision instead of rounding beyond EOF',()=>{
 const source=fs.readFileSync(new URL('../web/talking_panel.js',import.meta.url),'utf8');
 const start=source.indexOf('function refreshTimes()');
 const body=source.slice(start,source.indexOf('function selectedWindow(',start));
 const duration=175000/24000;
 const audio={getAttribute(){return this.src || '';}};
 const row={end:{value:duration.toFixed(6)},time:{},duration:{},metrics:{},clipAudio:audio};
 const player={getAttribute(){return this.src || '';}};
 const refresh=vm.runInNewContext(body+'\nrefreshTimes',{rows:[row],selected:0,plan:{duration},base:'/test',
  $:id=>id==='audio'?player:{}});
 refresh();const url=new URL(audio.src,'http://localhost');
 assert.equal(Number(url.searchParams.get('end')),duration);
 assert.ok(Number(url.searchParams.get('end'))<=duration);
 assert.equal(player.src,audio.src);
});
