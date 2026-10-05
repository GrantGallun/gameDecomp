// Offline DOM contract tests: no browser, network or model calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page(state) {
  class Element {
    constructor() { this.children=[]; this.handlers={}; this.value=''; this.classList={toggle(){},remove(){}}; }
    addEventListener(name, handler) { this.handlers[name]=handler; }
    replaceChildren() { this.children=[]; }
    append(child) { this.children.push(child); }
    set innerHTML(value) { throw Error('Model text must never be inserted as HTML'); }
  }
  const elements=new Map(), calls=[], intervals=[];
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  get('research-minutes').value='5'; get('research-limit').value='2'; get('research-model').value='qwen2.5-coder:14b';
  const context={document:{getElementById:get,createElement:()=>new Element(),hidden:false,addEventListener(){}},
    AbortSignal, setInterval:fn=>intervals.push(fn), fetch:async(url,options={})=>{
      calls.push({url,options}); return {ok:true,json:async()=>state.value};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../eval/progress_research.js'),'utf8'),context);
  return {get,calls,refresh:()=>intervals[0]()};
}
const tick=()=>new Promise(setImmediate);

test('start submits the selected bounds; an active run disables Start and enables Stop',async()=>{
  const state={value:{status:'off',active:false,controls_enabled:true}};
  const p=page(state); await tick();
  assert.equal(p.get('research-start').disabled,false);
  state.value={status:'running',active:true,controls_enabled:true,calls:1,max_calls:2};
  await p.get('research-start').handlers.click(); await tick();
  const sent=p.calls.find(c=>c.options.method==='POST');
  assert.equal(sent.url,'/api/research/control');
  assert.deepEqual(JSON.parse(sent.options.body),{action:'start',options:{model:'qwen2.5-coder:14b',minutes:5,max_calls:2}});
  assert.equal(sent.options.headers['X-Campaign-Control'],'ui');
  assert.equal(p.get('research-start').disabled,true);
  assert.equal(p.get('research-stop').disabled,false);
  state.value={status:'stopped',active:false,controls_enabled:true};
  await p.get('research-stop').handlers.click(); await tick();
  assert.deepEqual(JSON.parse(p.calls.at(-1).options.body),{action:'stop'});
  assert.equal(p.get('research-start').disabled,false);
});

test('untrusted experiment text stays text; read-only cannot start',async()=>{
  const title='<img src=x onerror=alert(1)>';
  const p=page({value:{status:'finished',active:false,controls_enabled:false,recent:[{
    proposal:{title,rationale:'note',before:'x',after:'y',metric:'calls',relation:'equal'},
    status:'synthetic_confirmed',measurements:[]}]}});
  await tick();
  assert.equal(p.get('research-start').disabled,true);
  assert.equal(p.get('research-history').children[0].children[0].textContent, title+' — synthetic confirmed');
});
