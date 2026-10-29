import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('host layout keeps management beside project selector and a separate working close button',()=>{
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.className='';this.classList={add(){}};}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    removeAttribute(){}
    querySelectorAll(selector){return this.children.flatMap(node=>[node,...node.querySelectorAll('*')]).filter(node=>selector==='*' || selector.split(',').some(part=>part.trim().startsWith('.') ? node.className.split(' ').includes(part.trim().slice(1)) : node.tag===part.trim()));}
    querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
  }
  const toolbar=new Element('div'),label=new Element('span'),select=new Element('select');
  const manage=new Element('button');manage.className='h3lv-button project-manager';manage.textContent='管理项目';
  const close=new Element('button');close.className='h3talk-close';close.textContent='关闭';
  let closed=0,opened=0;close.onclick=()=>closed++;manage.onclick=()=>opened++;
  toolbar.append(label,select,manage,close);
  const shade=new Element('div'),host=new Element('div');
  const frame=new Element('iframe');frame.closest=()=>shade;frame.parentElement=host;frame.previousElementSibling=toolbar;
  const parentDoc={createElement:tag=>new Element(tag),querySelector:()=>({})};
  const win={parent:{document:parentDoc},frameElement:frame,h3talkHasUnsavedChanges:()=>false};
  const source=fs.readFileSync(new URL('../web/talking_panel.js',import.meta.url),'utf8');
  const start=source.indexOf('function syncHost()'),end=source.indexOf('async function request(',start);
  vm.runInNewContext(source.slice(start,end)+'\nsyncHost();',{window:win,document:{body:new Element('body')},fail(){throw new Error('unexpected dirty form');}});
  assert.equal(toolbar.querySelector('.h3lv-project-row').children[2],manage);
  assert.equal(toolbar.querySelector('.h3lv-title-row').children[1],close);
  close.onclick();assert.equal(closed,1);assert.equal(opened,0);
  manage.onclick();assert.equal(opened,1);
});
