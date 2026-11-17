const runs = document.querySelector('#runs');
const validation = document.querySelector('#validation');
const state = document.querySelector('#validation-state');

function escapeText(value) {
  const node = document.createElement('span');
  node.textContent = String(value ?? '—');
  return node.innerHTML;
}

async function loadRuns() {
  runs.innerHTML = '<p class="muted">读取中…</p>';
  try {
    const response = await fetch('/api/v1/runs');
    const data = await response.json();
    if (!data.runs.length) {
      runs.innerHTML = '<p class="muted">归档根目录中没有可识别运行。</p>';
      return;
    }
    runs.innerHTML = data.runs.map((run) => `
      <button class="run-card" data-run="${escapeText(run.run_id)}">
        <span><strong>${escapeText(run.run_id)}</strong><small>${escapeText(run.world_id)}</small></span>
        <span class="run-meta"><b>${escapeText(run.status)}</b><small>t = ${escapeText(run.simulation_time_s)} s</small></span>
      </button>`).join('');
  } catch (error) {
    runs.innerHTML = `<p class="error">${escapeText(error.message)}</p>`;
  }
}

document.querySelector('#refresh').addEventListener('click', loadRuns);
document.querySelector('#validate').addEventListener('click', async () => {
  validation.textContent = '';
  state.textContent = '后端验证中…';
  try {
    const config = JSON.parse(document.querySelector('#config').value);
    const response = await fetch('/api/v1/experiments/validate', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({config})
    });
    const data = await response.json();
    state.textContent = data.valid ? `有效 · ${data.sha256.slice(0, 12)}` : '验证失败';
    validation.textContent = JSON.stringify(data.valid ? data.resolved : data.errors, null, 2);
  } catch (error) {
    state.textContent = '输入不是有效 JSON';
    validation.textContent = error.message;
  }
});

loadRuns();

const jobDetails = document.querySelector('#job-details');
const jobState = document.querySelector('#job-state');
let activeRunId = null;
let pollHandle = null;

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || result.error || response.status);
  return result;
}

async function pollJob() {
  if (!activeRunId) return;
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(activeRunId)}`);
    if (!response.ok) throw new Error('当前进程没有该任务的活动记录');
    const info = await response.json();
    jobDetails.textContent = JSON.stringify(info, null, 2);
    jobState.textContent = `任务 ${info.run_id}: ${info.phase} · 物理状态 ${info.physical?.status ?? 'PENDING'}`;
    if (['COMPLETED','COLLISION','FAILED','CANCELLED','CANCELLED_BEFORE_START','PAUSED','WORKER_ERROR'].includes(info.phase)) {
      clearInterval(pollHandle);
      pollHandle = null;
      loadRuns();
    }
  } catch (error) {
    jobState.textContent = error.message;
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

function beginPoll(id) {
  activeRunId = id;
  clearInterval(pollHandle);
  pollJob();
  pollHandle = setInterval(pollJob, 1000);
}

document.querySelector('#start-run').addEventListener('click', async () => {
  try {
    const run_id = document.querySelector('#run-id').value;
    const initial_state = JSON.parse(document.querySelector('#initial-state').value);
    const settings = JSON.parse(document.querySelector('#long-settings').value);
    jobState.textContent = '提交真实M5A任务中…';
    const result = await apiPost('/api/v1/runs', {run_id, initial_state, settings});
    beginPoll(result.run_id);
  } catch (error) {
    jobState.textContent = `启动失败：${error.message}`;
  }
});

for (const action of ['pause','resume','cancel']) {
  document.querySelector(`#${action}-run`).addEventListener('click', async () => {
    const id = document.querySelector('#run-id').value;
    try {
      const result = await apiPost(`/api/v1/runs/${encodeURIComponent(id)}/${action}`, {});
      jobState.textContent = `服务端已接受${action}请求；物理状态需等待分段提交确认。`;
      beginPoll(result.run_id);
    } catch (error) {
      jobState.textContent = `请求失败：${error.message}`;
    }
  });
}

/* M5B-04a: exact committed M5A samples, client-side orientation only. */
const orbitCanvas = document.querySelector('#orbit-canvas');
const orbitContext = orbitCanvas.getContext('2d');
const orbitSlider = document.querySelector('#orbit-time');
const orbitText = document.querySelector('#orbit-time-text');
const orbitStatus = document.querySelector('#observe-status');
const orbitEvents = document.querySelector('#orbit-events');
let orbitSamples = [], orbitEventItems = [], orbitIndex = 0, orbitAzimuth = .6, orbitAltitude = .35;
let orbitTimer = null;
const orbitIds = ['star-1','star-2','star-3','planet-1'];
const orbitColors = ['#f3a44a','#e27459','#8bb7ff','#d7e9dc'];

function projectOrbit(position, scale, center) {
  const x=(position[0]-center[0])/scale, y=(position[1]-center[1])/scale;
  const z=(position[2]-center[2])/scale;
  const ca=Math.cos(orbitAzimuth), sa=Math.sin(orbitAzimuth);
  const ce=Math.cos(orbitAltitude), se=Math.sin(orbitAltitude);
  return [x*ca-y*sa, (x*sa+y*ca)*se-z*ce];
}
function orbitDraw() {
  const ctx=orbitContext, w=orbitCanvas.width, h=orbitCanvas.height;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle='#080c0d'; ctx.fillRect(0,0,w,h);
  if (!orbitSamples.length) {
    ctx.fillStyle='#8d9ba1'; ctx.fillText('尚无已提交轨道采样',22,32); return;
  }
  const end=orbitSamples[orbitIndex];
  const points=orbitSamples.flatMap(s=>orbitIds.map(id=>s.positions_m[id]));
  const center=[0,1,2].map(k=>points.reduce((sum,p)=>sum+p[k],0)/points.length);
  const extent=Math.max(1,...points.map(p=>Math.hypot(...p.map((v,k)=>v-center[k]))));
  const scale=extent/(Math.min(w,h)*.40);
  orbitIds.forEach((id,bi)=>{
    ctx.beginPath(); ctx.lineWidth=id==='planet-1'?1.6:1.2;
    ctx.strokeStyle=orbitColors[bi];
    orbitSamples.slice(0,orbitIndex+1).forEach((s,i)=>{
      const [px,py]=projectOrbit(s.positions_m[id],scale,center);
      const sx=w/2+px, sy=h/2+py;
      if(i===0)ctx.moveTo(sx,sy);else ctx.lineTo(sx,sy);
    }); ctx.stroke();
    const [px,py]=projectOrbit(end.positions_m[id],scale,center);
    ctx.beginPath();ctx.fillStyle=orbitColors[bi];
    ctx.arc(w/2+px,h/2+py,id==='planet-1'?4:7,0,Math.PI*2);ctx.fill();
    ctx.font='12px system-ui';ctx.fillText(id,w/2+px+9,h/2+py-7);
  });
  ctx.fillStyle='#b7c5ca';ctx.font='13px monospace';
  ctx.fillText('已提交采样；3D旋转视角；轨迹仅至所选时间',18,h-18);
}
function orbitSetIndex(i) {
  if(!orbitSamples.length)return;
  orbitIndex=Math.max(0,Math.min(orbitSamples.length-1,Math.trunc(i)));
  orbitSlider.value=orbitIndex;
  orbitText.textContent=orbitSamples[orbitIndex].t_s.toPrecision(9)+' SI s';
  orbitDraw();
}
function orbitStop(){if(orbitTimer!==null)clearInterval(orbitTimer);orbitTimer=null;}
async function loadOrbit() {
  orbitStop(); orbitStatus.textContent='正在验证并读取M5A已提交分段…';
  const id=document.querySelector('#observe-run-id').value;
  const params=new URLSearchParams();
  for(const [idName,key] of [['observe-start','start_s'],['observe-end','end_s']]){
    const value=document.getElementById(idName).value;
    if(value!=='')params.set(key,value);
  }
  params.set('max_points',document.querySelector('#observe-count').value);
  try {
    const base='/api/v1/runs/'+encodeURIComponent(id);
    const suffix=params.toString();
    const t=await fetch(base+'/trajectory?'+suffix);
    const trajectory=await t.json();
    if(!t.ok)throw Error(trajectory.message||trajectory.error);
    const e=await fetch(base+'/events?'+suffix.replace(/&?max_points=[^&]*/,''));
    const events=await e.json();
    if(!e.ok)throw Error(events.message||events.error);
    orbitSamples=trajectory.samples;
    orbitEventItems=events.events;
    orbitIndex=0;orbitSlider.min=0;
    orbitSlider.max=Math.max(0,orbitSamples.length-1);
    orbitStatus.textContent='世界 '+trajectory.world_id+' · '+trajectory.sample_count+
      ' 条真实采样，显示 '+trajectory.display_count+' 条 · '+trajectory.data_status+
      ' · 无外推 · 事件 '+events.event_count;
    orbitEvents.replaceChildren(new Option('选择事件',''));
    orbitEventItems.forEach((event,i)=>{
      orbitEvents.add(new Option(event.event_type+' @ '+event.t_s+' s',String(i)));
    });
    orbitSetIndex(orbitSamples.length-1);
    if(!orbitSamples.length)orbitDraw();
  } catch(error) {
    orbitSamples=[];orbitEventItems=[];
    orbitStatus.textContent='无法载入：'+error.message;
    orbitDraw();
  }
}
document.querySelector('#observe-load').addEventListener('click',loadOrbit);
for(const [id,axis,delta] of [['orbit-left','azimuth',-.2],['orbit-right','azimuth',.2],['orbit-up','altitude',.14],['orbit-down','altitude',-.14]]){
  document.getElementById(id).addEventListener('click',()=>{
    if(axis==='azimuth')orbitAzimuth+=delta;
    else orbitAltitude=Math.max(-1.45,Math.min(1.45,orbitAltitude+delta));
    orbitDraw();
  });
}
orbitSlider.addEventListener('input',()=>orbitSetIndex(Number(orbitSlider.value)));
document.querySelector('#orbit-play').addEventListener('click',()=>{
  orbitStop();
  orbitTimer=setInterval(()=>{
    if(orbitIndex>=orbitSamples.length-1){orbitStop();return;}
    orbitSetIndex(orbitIndex+1);
  },80);
});
document.querySelector('#orbit-stop').addEventListener('click',orbitStop);
orbitEvents.addEventListener('change',()=>{
  if(orbitEvents.value===''||!orbitSamples.length)return;
  const event=orbitEventItems[Number(orbitEvents.value)];
  let nearest=0,delta=Infinity;
  orbitSamples.forEach((sample,i)=>{
    const difference=Math.abs(sample.t_s-event.t_s);
    if(difference<delta){delta=difference;nearest=i;}
  });
  orbitSetIndex(nearest);
  document.querySelector('#orbit-event-detail').textContent=
    event.event_type+' ('+event.body_ids.join(', ')+') @ '+event.t_s+' s · '+
    event.message+' · 采样时间偏差 '+delta.toPrecision(5)+' s';
});
document.querySelector('#runs').addEventListener('click',event=>{
  const button=event.target.closest('[data-run]');
  if(!button)return;
  document.querySelector('#observe-run-id').value=button.dataset.run;
  document.querySelector('#run-id').value=button.dataset.run;
});
orbitDraw();
