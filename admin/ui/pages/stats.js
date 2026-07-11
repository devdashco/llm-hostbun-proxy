import { html, useState, useEffect, useCallback, api, nfmt, usd, ago, fmtMs, fmtTime, seriesColor, Chip, ProviderPill, KV, Card, CardHead, Chart, Tabs, PageHead, useApp } from "../core.js";

/* ───────── STATS ───────── */
const WIN_ITEMS=[['15m','Last 15 min'],['1h','Last hour'],['6h','Last 6h'],['24h','Last 24h'],['7d','Last 7d'],['30d','Last 30d'],['all','All time']];
function Stats(){
  const {go,gotoCalls} = useApp();
  const [win,setWin]=useState('24h');
  const [s,setS]=useState(null);
  const [series,setSeries]=useState(null);
  const [metric,setMetric]=useState('tok'); const [by,setBy]=useState('provider');
  const [sort,setSort]=useState({key:'tok',dir:-1});
  const load=useCallback(async()=>{ try{ setS(await api('stats?window='+encodeURIComponent(win))); }catch(e){} },[win]);
  useEffect(()=>{load();},[load]);
  useEffect(()=>{ (async()=>{ try{ setSeries(await api('series?window='+encodeURIComponent(win)+'&by='+by)); }catch(e){} })(); },[win,by]);
  return html`
  <${PageHead} title="Usage" desc="Where the tokens went: by provider, project, client and model."
    onRefresh=${load}
    actions=${html`<${Tabs} val=${win} onChange=${setWin} items=${WIN_ITEMS.map(([v,l])=>[v,l.replace(/^Last /,'')])}/>`}/>
  ${!s?html`<div class="mut">loading…</div>`: s.dbReady===false?html`<div class="alert bad">The call DB is unavailable, so there is nothing to summarise.</div>`: html`
  ${(()=>{ const inT=s.windowPromptTokens||0,outT=s.windowCompletionTokens||0,tot=s.windowTokens||0; const avg=s.windowCalls>0?Math.round(tot/s.windowCalls):0; const lbl=(WIN_ITEMS.find(w=>w[0]===s.window)||[])[1]||s.window;
    return html`<div class="grid">
      <${KV} n=${'Tokens ('+lbl+')'}>${tot.toLocaleString()}<//>
      <${KV} n="In → Out">${nfmt(inT)} <span class="mut">→</span> ${nfmt(outT)}<//>
      <${KV} n="Avg / call">${avg.toLocaleString()}<//>
      <${KV} n="Est. cost">${usd(s.windowCost)} <small class="hint">crazyrouter</small><//>
      <${KV} n="Calls">${s.windowCalls.toLocaleString()}<//>
      <${KV} n="Errors">${(s.windowErrors||0)}${s.windowJsonFails?html` <small class="hint">(${s.windowJsonFails} refusal)</small>`:''}<//>
      <${KV} n="Total ever">${s.total.toLocaleString()}<//>
      <${KV} n="Oldest">${s.oldest?fmtTime(s.oldest):'—'}<//>
    </div>`; })()}
  <${Card}>
    <${CardHead} title="History"
      actions=${html`
        <${Tabs} val=${metric} onChange=${setMetric} items=${[['tok','Tokens'],['n','Calls'],['err','Errors']]}/>
        <${Tabs} val=${by} onChange=${setBy} items=${[['provider','provider'],['project','project'],['model','model']]}/>`}/>
    ${series?html`<${Chart} data=${series} metric=${metric} by=${by} H=${240}/>`:html`<span class="mut">loading…</span>`}
  </${Card}>
  <${Card}>
    <${CardHead} title="Share by provider" hint="What fraction of calls, and of tokens, each provider carried."/>
    ${(()=>{ const providers=s.byProvider||[]; const totN=providers.reduce((a,r)=>a+r.n,0)||1, totT=providers.reduce((a,r)=>a+r.tok,0)||1;
      return providers.length?providers.map((r,i)=>{ const c=seriesColor(r.provider,i),cp=r.n/totN*100,tp=r.tok/totT*100;
        return html`<div class="lblrow"><span class="nm"><span class="swatch" style="background:${c}"></span>${r.provider}</span>
          <span class="bar"><i style="width:${cp.toFixed(1)}%;background:${c}"></i></span>
          <span class="vv">${r.n} calls (${cp.toFixed(0)}%) · ${nfmt(r.tok)} tok (${tp.toFixed(0)}%)</span></div>`;
      }):html`<span class="mut">No traffic in this window.</span>`; })()}
  </${Card}>
  <${Card}>
    <${CardHead} title="By project" hint=${html`Click a row to see its calls. <code>(none)</code> means the caller sent no project.`}/>
    <div class="tablewrap">${ProjectTable({s,sort,setSort,gotoCalls})}</div>
  </${Card}>
  <${Card}>
    <${CardHead} title="By client" hint=${html`Who is calling, by user-agent. <b>Thinkers</b> are calls that spent reasoning effort or extended thinking. Claude Code appears as <code>claude-cli/…</code>. Click a row to filter the call log.`}/>
    <div class="tablewrap"><table>
      <tr><th>client · user-agent</th><th>calls</th><th>tokens</th><th>thinkers</th><th>IPs</th><th>providers</th><th>last</th></tr>
      ${(s.byClient||[]).map(r=>html`<tr class="click" onClick=${()=>gotoCalls({q:r.ua})}>
        <td class="mono" style="font-size:12px">${r.ua}</td>
        <td class="mono">${r.n}</td><td class="mono">${(r.tok||0).toLocaleString()}</td>
        <td class="mono" style=${r.thinkers>0?'color:var(--warn);font-weight:600':'color:var(--fg-mut)'}>${r.thinkers||0}</td>
        <td class="mono">${r.ips}</td><td class="mut" style="font-size:11.5px">${String(r.providers||'').split(',').join(' ')}</td>
        <td class="mono mut" style="font-size:11px">${ago(r.last)}</td>
      </tr>`)}
    </table></div>
  </${Card}>
  <${Card}>
    <${CardHead} title="By model" hint="Estimated cost is crazyrouter only; claudecode is flat-rate."/>
    <div class="tablewrap"><table>
      <tr><th>requested model</th><th>provider</th><th>calls</th><th>tokens</th><th>in → out</th><th>est $</th><th>avg</th></tr>
      ${(s.byModel||[]).map(r=>html`<tr><td class="mono" style="font-size:12px">${r.req_model||'-'}</td><td><${ProviderPill} provider=${r.provider}/></td><td class="mono">${r.n}</td><td class="mono">${(r.tok||0).toLocaleString()}</td><td class="mono mut" style="font-size:12px">${nfmt(r.ptok)} → ${nfmt(r.ctok)}</td><td class="mono">${usd(r.usd)}</td><td class="mono">${fmtMs(r.avg_ms)}</td></tr>`)}
    </table></div>
  </${Card}>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px">
    <${Card}><${CardHead} title="By provider"/><div class="tablewrap"><table><tr><th>provider</th><th>calls</th><th>tokens</th><th>avg</th><th>err</th></tr>
      ${(s.byProvider||[]).map(r=>html`<tr><td><${ProviderPill} provider=${r.provider}/></td><td class="mono">${r.n}</td><td class="mono">${(r.tok||0).toLocaleString()}</td><td class="mono">${fmtMs(r.avg_ms)}</td><td class="mono" style=${r.errors>0?'color:var(--danger)':''}>${r.errors??'—'}</td></tr>`)}
    </table></div></${Card}>
    <${Card}><${CardHead} title="By account"/><div class="tablewrap"><table><tr><th>key</th><th>calls</th></tr>
      ${(s.byKey||[]).map(r=>html`<tr><td class="mono" style="font-size:12px">${r.key_label||''}</td><td class="mono">${r.n}</td></tr>`)}
    </table></div></${Card}>
  </div>
  `}`;
}
function ProjectTable({s,sort,setSort,gotoCalls}){
  const rows=[...(s.byProject||[])]; const maxT=Math.max(1,...rows.map(r=>r.tok||0));
  const k=sort.key,d=sort.dir;
  rows.sort((a,b)=>{ let x,y;
    if(k==='project'){return d*String(a.project||'').localeCompare(String(b.project||''));}
    if(k==='io'){x=a.ptok||0;y=b.ptok||0;} else if(k==='errors'){x=a.n?a.errors/a.n:0;y=b.n?b.errors/b.n:0;} else {x=a[k]||0;y=b[k]||0;}
    return d*((x>y?1:x<y?-1:0));
  });
  const cols=[['project','project'],['n','calls'],['tok','tokens'],['io','in → out'],['usd','est $'],['avg_ms','avg'],['errors','err%'],[null,'providers'],['last','last seen'],[null,'share']];
  const onSort=key=>{ if(!key)return; setSort(sort.key===key?{key,dir:-sort.dir}:{key,dir:(key==='project'?1:-1)}); };
  return html`<table>
    <tr>${cols.map(([key,lbl])=>html`<th class=${key?'sortable '+(key===k?'on':''):''} onClick=${()=>onSort(key)}>${lbl}${key===k?(d<0?' ▾':' ▴'):''}</th>`)}</tr>
    ${rows.map(r=>{ const errPct=r.n>0?(r.errors/r.n*100):0; const share=r.tok/maxT*100;
      let badge=null; if(r.limit&&r.limitPct!=null){ const sp=r.limit.slowPct||95; const col=r.limitPct>=100?'var(--danger)':r.limitPct>=sp?'var(--warn)':'var(--ok)'; const cap=r.limit.tokens>0?nfmt(r.limit.tokens)+' tok':r.limit.calls>0?r.limit.calls+' calls':'';
        badge=html` <${Chip} title=${r.limitPct+'% of '+cap+'/'+r.limit.window+' · at 100%: '+r.limit.hard} style=${`color:${col};border-color:${col}`}>${r.limitPct}% ${r.limit.hard}<//>`; }
      return html`<tr class="click" onClick=${()=>gotoCalls({project:(r.project&&r.project!=='(none)')?r.project:''})}>
        <td class="mono" style="font-size:12px">${r.project||'(none)'}${badge}</td>
        <td class="mono">${r.n}</td><td class="mono">${(r.tok||0).toLocaleString()}</td>
        <td class="mono mut" style="font-size:12px">${nfmt(r.ptok)} → ${nfmt(r.ctok)}</td>
        <td class="mono">${usd(r.usd)}</td><td class="mono">${fmtMs(r.avg_ms)}</td>
        <td class="mono" style=${r.errors>0?'color:var(--red);font-weight:700':'color:var(--mut)'}>${errPct.toFixed(errPct&&errPct<10?1:0)}%</td>
        <td style="font-size:11px">${String(r.providers||'').split(',').join(' ')}</td>
        <td class="mono mut" style="font-size:11px">${ago(r.last)}</td>
        <td><span class="bar"><i style="width:${share.toFixed(1)}%;background:var(--acc)"></i></span></td>
      </tr>`; })}
  </table>`;
}


export { WIN_ITEMS, Stats, ProjectTable };
