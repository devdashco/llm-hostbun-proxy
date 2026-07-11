// The shell: nav, routing, login, and the page table. Every page the panel has is named here — a
// page that exists but is unreachable would be obvious in this one file.
import {
  html, render, useState, useEffect, useCallback,
  api, setOnUnauth, Svg, NAV, BASE, slugFor, nameFor, Ctx,
} from "./core.js";
import { CallDrawer } from "./drawer.js";
import { Overview } from "./pages/overview.js";
import { Calls } from "./pages/calls.js";
import { Consumers } from "./pages/consumers.js";
import { Stats } from "./pages/stats.js";
import { Accounts } from "./pages/accounts.js";
import { Routing } from "./pages/routing.js";
import { Models } from "./pages/models.js";
import { Crazyrouter } from "./pages/crazyrouter.js";
import { Secrets } from "./pages/secrets.js";

const PAGES = { overview: Overview, calls: Calls, consumers: Consumers, stats: Stats,
                accounts: Accounts, routing: Routing, models: Models, crazyrouter: Crazyrouter, secrets: Secrets };

function Shell({slug,go,children}){
  const [sbOpen,setSbOpen]=useState(false);
  return html`<div class="shell">
    <aside class=${'sidebar'+(sbOpen?' open':'')}>
      <div class="sb-brand"><div class="logo">hb</div><div><div class="t">hostbun</div><div class="s">llm router · control panel</div></div></div>
      <nav class="nav">
        ${NAV.map(it=>it.sec?html`<div class="nav-sec">${it.sec}</div>`:html`
          <a class=${'nav-item'+(it.slug===slug?' on':'')} href=${BASE+'/'+it.slug} onClick=${e=>{e.preventDefault();go(it.slug);setSbOpen(false);}}>
            <${Svg} n=${it.icon}/><span>${it.name}</span>
          </a>`)}
      </nav>
      <div class="sb-foot"><button class="ghost sm" style="width:100%" onClick=${async()=>{ try{await api('logout',{method:'POST'});}catch{} location.reload(); }}>Sign out</button></div>
    </aside>
    ${sbOpen&&html`<div class="dov open" style="z-index:49" onClick=${()=>setSbOpen(false)}></div>`}
    <div class="main">
      ${/* Mobile only. On desktop the sidebar carries the brand and <PageHead/> carries the title,
           so a second bar repeating the page name is chrome with nothing to say. */''}
      <header class="topbar">
        <button class="ghost sm" onClick=${()=>setSbOpen(!sbOpen)} aria-label="menu">☰</button>
        <h3>${nameFor(slug)}</h3>
      </header>
      <div class="content">${children}</div>
    </div>
  </div>`;
}

/* ───────── login ───────── */
function Login({onOk}){
  const [pw,setPw]=useState(''); const [err,setErr]=useState('');
  async function submit(){ setErr(''); try{ await api('login',{method:'POST',body:JSON.stringify({password:pw})}); onOk(); }catch(e){ setErr(e.message); } }
  return html`<div class="login">
    <div class="sb-brand"><div class="logo">hb</div></div>
    <h1>llm.hostbun.cc</h1>
    <p class="hint">Control panel for the router. Every model call we make goes through it.</p>
    <div class="card">
      <label for="pw" style="margin-top:0">Password</label>
      <input id="pw" type="password" placeholder="••••••" autofocus value=${pw} onInput=${e=>setPw(e.target.value)} onKeyDown=${e=>e.key==='Enter'&&submit()}/>
      <div style="height:12px"></div>
      <button style="width:100%" onClick=${submit}>Sign in</button>
      ${err&&html`<p class="down" style="margin:12px 0 0;font-size:13px">${err}</p>`}
    </div>
  </div>`;
}

/* ───────── root ───────── */
function App(){
  const [authed,setAuthed]=useState(null); // null=checking
  const [state,setState]=useState(null);
  const [slug,setSlug]=useState(()=>{ let s=location.pathname.replace(BASE,'').replace(/^\/+/,'').split('/')[0]||'overview'; return NAV.some(x=>x.slug===s)?s:'overview'; });
  const [callId,setCallId]=useState(null);
  const boot=useCallback(async()=>{ try{ const s=await api('state'); setState(s); setAuthed(true); }catch(e){ setAuthed(false); } },[]);
  useEffect(()=>{ setOnUnauth(()=>setAuthed(false)); boot(); },[boot]);
  useEffect(()=>{ const h=()=>{ let s=location.pathname.replace(BASE,'').replace(/^\/+/,'').split('/')[0]||'overview'; setSlug(NAV.some(x=>x.slug===s)?s:'overview'); }; window.addEventListener('popstate',h); return ()=>window.removeEventListener('popstate',h); },[]);
  const go=useCallback(s=>{ history.pushState({},'',BASE+'/'+s); setSlug(s); document.title='hostbun · '+nameFor(s); },[]);
  useEffect(()=>{ document.title='hostbun · '+nameFor(slug); },[slug]);
  const reload=useCallback(s=>{ if(s)setState(s); else boot(); },[boot]);

  if(authed===null) return html`<div class="mut" style="padding:40px;text-align:center">…</div>`;
  if(!authed) return html`<${Login} onOk=${boot}/>`;
  if(!state) return html`<div class="mut" style="padding:40px;text-align:center">loading…</div>`;
  const Page=PAGES[slug]||Overview;
  // gotoCalls: drill into the call log with a filter (project= or q=). Calls remounts on nav and reads these.
  const gotoCalls=(params)=>{ try{ const u=new URL(location.href); u.searchParams.delete('project'); u.searchParams.delete('q'); Object.entries(params||{}).forEach(([k,v])=>{ if(v) u.searchParams.set(k,v); }); history.replaceState({},'',u); }catch{} go('calls'); };
  const ctx={ state, reload, go, openCall:setCallId, gotoCalls };
  return html`<${Ctx.Provider} value=${ctx}>
    <${Shell} slug=${slug} go=${go}><${Page}/></${Shell}>
    <${CallDrawer} id=${callId} onClose=${()=>setCallId(null)}/>
  </${Ctx.Provider}>`;
}

render(html`<${App}/>`, document.getElementById("root"));
