"""Single-page household console: rules, schedules, runs, findings."""

HOME_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Valle Home</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e2e2dc;--accent:#2563eb;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--muted:#9a9a94;--card:#20201e;--line:#33332f;--accent:#7aa2ff;--bad:#ff8a7a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:16px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h1{font-size:22px}h2{font-size:16px;margin:0 0 10px}
textarea{width:100%;box-sizing:border-box;min-height:90px;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px}
button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid var(--line);background:var(--accent);color:#fff;cursor:pointer}
.row{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line);flex-wrap:wrap}
.muted{color:var(--muted)}.bad{color:var(--bad);font-weight:600}
label{display:block;margin:8px 0 4px;font-weight:600}
</style></head><body><main>
<h1>Valle Home</h1>
<section><h2>Household rules and places</h2>
<label for="rules">Rules (one per line)</label><textarea id="rules"></textarea>
<label for="places">Places (name: description, one per line)</label><textarea id="places"></textarea>
<p><button id="save">Save</button> <span id="saved" class="muted"></span></p></section>
<section><h2>Scheduled missions</h2><div id="missions" class="muted">Loading…</div></section>
<section><h2>Recent runs</h2><div id="runs" class="muted">Loading…</div></section>
<section><h2>Findings</h2><div id="findings" class="muted">Loading…</div></section>
</main><script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const j=(u,o)=>fetch(u,o).then(r=>r.json());
let household={vacuum:{}};
async function loadHousehold(){household=(await j("/household")).household;
 rules.value=household.rules.join("\\n");
 places.value=Object.entries(household.places).map(([k,v])=>k+": "+v).join("\\n");}
save.onclick=async()=>{const p={};places.value.split("\\n").forEach(l=>{const i=l.indexOf(":");if(i>0)p[l.slice(0,i).trim()]=l.slice(i+1).trim();});
 const r=await j("/household",{method:"PUT",headers:{"content-type":"application/json"},
  body:JSON.stringify({rules:rules.value.split("\\n").map(s=>s.trim()).filter(Boolean),places:p,vacuum:household.vacuum})});
 saved.textContent=r.ok?"Saved":r.error;};
async function loadMissions(){const m=(await j("/missions")).missions;
 missions.innerHTML=m.length?m.map(e=>`<div class="row"><b>${esc(e.schedule)}</b> ${esc(e.mission.goal)}
 <span class="muted">${esc(e.mission.skill||"")} · last: ${esc(e.last_run_status||"never")}</span>
 <button data-id="${esc(e.id)}" data-on="${!e.enabled}">${e.enabled?"Pause":"Resume"}</button></div>`).join(""):"No scheduled missions.";
 missions.querySelectorAll("button").forEach(b=>b.onclick=async()=>{await j("/missions/"+b.dataset.id,{method:"PATCH",
  headers:{"content-type":"application/json"},body:JSON.stringify({enabled:b.dataset.on==="true"})});loadMissions();});}
async function loadRuns(){const r=(await j("/runs?limit=15")).runs;
 runs.innerHTML=r.length?r.map(x=>{const res=x.result||{};const bad=x.status==="failed"||res.needs_followup;
  const ev=(x.evidence||[]).map(e=>`<a href="/evidence/${esc(x.run_id)}/${esc(e.file)}">${esc(e.label)}</a>`).join(" ");
  return `<div class="row"><span class="${bad?"bad":""}">${esc(res.state||x.status)}</span> ${esc(x.mission?.goal)}
  <span class="muted">${esc(x.started_at)}</span><div style="width:100%">${esc(res.summary||x.error||"")} ${ev}</div></div>`}).join(""):"No runs yet.";}
async function loadFindings(){const f=(await j("/findings")).findings;
 findings.innerHTML=f.length?f.map(x=>`<div class="row"><span class="${x.kind==="hazard"?"bad":""}">${esc(x.kind)}</span>
  ${esc(x.item)} <span class="muted">${esc(x.location)} · ${esc(x.at)}</span><div style="width:100%">${esc(x.detail)}</div></div>`).join(""):"No findings yet.";}
loadHousehold();loadMissions();loadRuns();loadFindings();
</script></body></html>"""
