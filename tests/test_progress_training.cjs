// Offline DOM contract tests for the training panel: no browser, network or training.
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
  get('training-steps-limit').value='120'; get('training-minutes').value='25';
  get('training-examples-limit').value='4000';
  const context={document:{getElementById:get,createElement:()=>new Element(),hidden:false,addEventListener(){}},
    AbortSignal, setInterval:fn=>intervals.push(fn), fetch:async(url,options={})=>{
      calls.push({url,options}); return {ok:true,json:async()=>state.value};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../eval/progress_training.js'),'utf8'),context);
  return {get,calls,refresh:()=>intervals[0]()};
}
const tick=()=>new Promise(setImmediate);

test('start submits the chosen limits and an active run disables Start',async()=>{
  const state={value:{status:'off',active:false,controls_enabled:true}};
  const p=page(state); await tick();
  assert.equal(p.get('training-start').disabled,false);
  state.value={status:'running',active:true,controls_enabled:true,steps:20,max_steps:120};
  await p.get('training-start').handlers.click(); await tick();
  const sent=p.calls.find(c=>c.options.method==='POST');
  assert.equal(sent.url,'/api/training/control');
  assert.deepEqual(JSON.parse(sent.options.body),
    {action:'start',options:{max_steps:120,minutes:25,max_examples:4000}});
  assert.equal(sent.options.headers['X-Campaign-Control'],'ui');
  assert.equal(p.get('training-start').disabled,true);
  assert.equal(p.get('training-stop').disabled,false);
  state.value={status:'stopped',active:false,controls_enabled:true};
  await p.get('training-stop').handlers.click(); await tick();
  assert.deepEqual(JSON.parse(p.calls.at(-1).options.body),{action:'stop'});
});

test('progress comes from the worker state and only a completed run names an adapter',async()=>{
  const p=page({value:{status:'stopped',active:false,controls_enabled:true,stage:'training',
    steps:41,max_steps:120,examples_used:512,max_examples:4000,elapsed_seconds:900,
    max_seconds:1500,last_loss:0.25,terminal_reason:'stopped by the user',
    published_adapter:'/home/grant/decomp/adapters/run-1'}});
  await tick();
  assert.equal(p.get('training-steps').textContent,'41 / 120');
  assert.equal(p.get('training-examples').textContent,'512 / 4000');
  assert.equal(p.get('training-time').textContent,'15 / 25 min');
  assert.equal(p.get('training-stage').textContent,'training');
  assert.equal(p.get('training-reason').textContent,'Terminal reason: stopped by the user');
  assert.match(p.get('training-adapter').textContent,/No adapter is published/);
  p.get('training-adapter').textContent='';
  // The same numbers with a verified receipt are allowed to name the artifact, and the
  // name is still qualified: this panel never claims the adapter is better.
  const done={value:{status:'completed',active:false,controls_enabled:true,steps:120,max_steps:120,
    examples_used:4000,max_examples:4000,elapsed_seconds:1500,max_seconds:1500,
    published_adapter:'/home/grant/decomp/adapters/run-1'}};
  const q=page(done); await tick();
  assert.match(q.get('training-adapter').textContent,
    /^\/home\/grant\/decomp\/adapters\/run-1 \(unverified: evaluate before any promotion\)$/);
});

test('read-only disables the training controls and untrusted text stays text',async()=>{
  const p=page({value:{status:'failed',active:false,controls_enabled:false,
    terminal_reason:'<img src=x onerror=alert(1)>'}});
  await tick();
  assert.equal(p.get('training-start').disabled,true);
  assert.equal(p.get('training-stop').disabled,true);
  assert.equal(p.get('training-reason').textContent,
    'Terminal reason: <img src=x onerror=alert(1)>');
});
