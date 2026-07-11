import { html, useState, useEffect, useCallback, api, toast, KV, Card, CardHead, PageHead, useApp } from "../core.js";

/* ───────── CRAZYROUTER ───────── */
function Crazyrouter(){
  const {reload}=useApp();
  const [c,setC]=useState(null); const [nk,setNk]=useState(''); const [test,setTest]=useState(null);
  const load=useCallback(async()=>{ setC('loading'); try{ setC(await api('crazyrouter')); }catch(e){ setC(null); } },[]);
  useEffect(()=>{load();},[load]);
  async function testKey(){ const key=nk.trim(); if(!key){toast('paste a key',true);return;} setTest('testing…');
    try{ const r=await api('crazyrouter/test',{method:'POST',body:JSON.stringify({key})}); setTest(JSON.stringify(r,null,2)); toast(r.keyValid?'key is VALID — click Save key':'key is INVALID',!r.keyValid); }catch(e){ setTest('error: '+e.message); } }
  async function saveKey(){ const key=nk.trim(); if(!key){toast('paste a key',true);return;} try{ const r=await api('config',{method:'POST',body:JSON.stringify({crazyrouterKey:key})}); reload(r.state); setNk(''); toast('key saved (live)'); load(); }catch(e){toast(e.message,true);} }
  return html`
  <${PageHead} title="Crazyrouter" desc="The cloud relay, and the only provider that bills per token." onRefresh=${load}/>
  ${c==='loading'||c==null?html`<div class="alert">${c==null?'Crazyrouter is unreachable.':'Checking…'}</div>`:html`
    <div class="grid">
      <${KV} n="Key">${c.keySet?(c.keyValid?html`<span class="up">valid</span>`:html`<span class="down">INVALID</span>`):html`<span class="down">not set</span>`}<//>
      <${KV} n="Limit">${c.hardLimitUsd!=null?'$'+c.hardLimitUsd:'—'}<//>
      <${KV} n="Used">${c.totalUsageUsd!=null?'$'+c.totalUsageUsd.toFixed(2):'—'}<//>
      <${KV} n="Remaining">${c.remainingUsd!=null?'$'+c.remainingUsd.toFixed(2):'—'}<//>
      <${KV} n="Models">${c.modelCount??'—'}<//>
      <${KV} n="Key id">${c.keyMasked||'(none)'}<//>
    </div>
    ${(c.message||!c.keyValid)&&html`<div class="alert warn">${(c.message||'key check failed')+(c.statuses?' · statuses '+JSON.stringify(c.statuses):'')}</div>`}`}
  <${Card}>
    <${CardHead} title="Update key" hint=${html`Paste a new <code>sk-</code> key and test it before saving. Saving takes effect immediately, with no redeploy.`}/>
    <input placeholder="sk-…" value=${nk} onInput=${e=>setNk(e.target.value)}/>
    <div class="flex" style="margin-top:12px"><button class="ghost" onClick=${testKey}>Test key</button><button onClick=${saveKey}>Save key</button></div>
    ${test!=null&&html`<pre>${test}</pre>`}
  </${Card}>`;
}


export { Crazyrouter };
