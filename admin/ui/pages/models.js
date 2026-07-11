import { html, h, useState, useEffect, useCallback, api, toast, ago, Pill, KV, Card, CardHead, PageHead } from "../core.js";

/* ───────── MODELS & TEST ───────── */
/* The Claude catalog: the ids the router will route to Anthropic.
   `advertised` is what /v1/models hands callers. `source:anthropic` means we reconciled it against
   api.anthropic.com; `seed` means that call has never succeeded and we are serving the hardcoded
   floor. These are Claude Max subscriptions — the pool serves whatever Claude Code serves, so there
   is no per-account "which answers" probe: a 429 is a usage window, not a missing model. */
function ClaudeCatalog(){
  const [cat,setCat]=useState(null); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{ try{ setCat(await api('claudecode/models')); }catch(e){ toast(e.message,true); } },[]);
  useEffect(()=>{load();},[load]);
  async function refresh(){ setBusy(true); try{ setCat(await api('claudecode/models',{method:'POST'})); toast('catalog refreshed from Anthropic'); }catch(e){ toast(e.message,true); } finally{ setBusy(false); } }
  const since=ts=>ts?ago(ts)+' ago':'never';
  return html`
  <${Card}>
    <${CardHead} title="Claude catalog" hint="Read from api.anthropic.com, not from config."
      actions=${html`<button class="ghost sm" disabled=${busy} onClick=${refresh}>${busy?'Refreshing…':'Refresh from Anthropic'}</button>`}/>
    <div class="grid">
      <${KV} n="advertised on /v1/models">${cat?cat.advertised.length:'…'} ids<//>
      <${KV} n="source">${cat?html`<${Pill} cls=${cat.source==='anthropic'?'up':'warnp'}>${cat.source}<//>`:'…'}<//>
      <${KV} n="last checked">${cat?since(cat.checkedAt):'…'}${cat&&cat.sweptAccounts&&cat.sweptAccounts.length?html` <span class="hint">swept ${cat.sweptAccounts.length} account${cat.sweptAccounts.length===1?'':'s'}</span>`:''}<//>
    </div>
    ${cat&&cat.source!=='anthropic'&&html`<p class="down" style="margin:0 0 10px;font-size:13px">Serving the hardcoded seed — Anthropic's catalog has not been read successfully${cat.error?': '+cat.error:''}. Ids still route; the list may just be stale.</p>`}
    ${cat&&cat.failedAccounts&&cat.failedAccounts.length>0&&html`<p class="warnp" style="margin:0 0 10px;font-size:13px">Could not read the catalog on ${cat.failedAccounts.map(f=>f.account+' ('+f.error+')').join(', ')}. Any model only those accounts can see is missing from this list.</p>`}
    <p class="hint" style="margin:0 0 12px">The catalog is a <b>union</b> across every account. All ids route to the pinned subscription; a <code>429</code> at request time means that subscription's usage window is spent (and resets), not that the model is gone.</p>
    <div class="mono" style="max-height:340px;overflow:auto;border:1px solid var(--border);border-radius:var(--r);padding:6px 10px">
      ${cat?cat.advertised.map(id=>{ const m=(cat.models||[]).find(x=>x.id===id);
        const na=(m&&m.accounts)?m.accounts.length:0, tot=(cat.sweptAccounts||[]).length;
        return html`<div class="flex" key=${id} style="gap:8px;padding:4px 0;align-items:center">
          <span>${id}</span>
          <span class="mut">${m&&m.display_name?'· '+m.display_name:''}</span>
          ${m&&tot>0&&na<tot?html`<${Pill} cls="warnp" title=${'only on: '+m.accounts.join(', ')}>${na}/${tot} accts<//>`:''}
          ${!m&&(cat.aliases||[]).includes(id)?html`<${Pill} cls="neutral" title="Anthropic serves this id but does not list it in /v1/models">alias<//>`:''}
          ${!m&&!(cat.aliases||[]).includes(id)&&cat.source==='anthropic'?html`<${Pill} cls="neutral" title="in the code seed, not in Anthropic's catalog">seed only<//>`:''}
        </div>`; }):html`<span class="mut">loading…</span>`}
    </div>
  </${Card}>`;
}

function Models(){
  const [models,setModels]=useState(null);
  const [filter,setFilter]=useState('');
  const [tm,setTm]=useState(''); const [tp,setTp]=useState('In one short sentence, what model are you?');
  const [out,setOut]=useState(null);
  useEffect(()=>{ (async()=>{ try{ setModels(await api('models')); }catch(e){} })(); },[]);
  async function runTest(){ const model=tm.trim(); if(!model){toast('enter a model',true);return;} setOut('running…');
    try{ const r=await api('test',{method:'POST',body:JSON.stringify({model,prompt:tp})}); setOut(`provider=${r.provider} sent=${r.sentModel} status=${r.status} ${r.ms}ms\n`+(r.content!=null?('\n'+r.content):('\n[no content]\n'+(r.error||r.raw||'')))); }
    catch(e){ setOut('error: '+e.message); } }
  const all=models?[...models.local,...models.claudecode,...models.crazyrouter]:[];
  const sec=(title,cls,arr)=>{ const items=(arr||[]).filter(m=>!filter||m.id.toLowerCase().includes(filter.toLowerCase())); if(!items.length)return '';
    return html`<div style="margin:14px 0 6px"><${Pill} cls=${cls}>${title}<//> <span class="mut">${items.length}</span></div>${items.map(m=>html`<div style="padding:3px 0">${m.id} <span class="mut">· ${m.owned_by||''}</span></div>`)}`; };
  return html`
  <${PageHead} title="Models & test" desc="What each provider advertises, and what the pinned subscription will actually serve."/>
  <${ClaudeCatalog}/>
  <${Card}>
    <${CardHead} title="Test a model" hint="Runs a real chat completion through the current routing."/>
    <div class="row">
      <input list="modellist" style="flex:2;min-width:220px" placeholder="model e.g. local / claude-sonnet-4-6 / gemini-2.5-pro" value=${tm} onInput=${e=>setTm(e.target.value)}/>
      <button style="flex:0 0 auto" onClick=${runTest}>Run</button>
    </div>
    <datalist id="modellist">${all.map(m=>html`<option value=${m.id}></option>`)}</datalist>
    <input placeholder="prompt" value=${tp} onInput=${e=>setTp(e.target.value)} style="margin-top:8px"/>
    ${out!=null&&html`<pre>${out}</pre>`}
  </${Card}>
  <${Card}>
    <${CardHead} title="Available models"
      hint=${models?`local ${models.local.length} · claudecode ${models.claudecode.length} · crazyrouter ${models.crazyrouter.length}`:''}/>
    <input placeholder="filter…" value=${filter} onInput=${e=>setFilter(e.target.value)}/>
    <div class="mono" style="max-height:420px;overflow:auto;margin-top:10px">${models?html`${sec('local','local',models.local)}${sec('claudecode','claudecode',models.claudecode)}${sec('crazyrouter','crazyrouter',models.crazyrouter)}`:html`<span class="mut">loading…</span>`}</div>
  </${Card}>`;
}


export { ClaudeCatalog, Models };
