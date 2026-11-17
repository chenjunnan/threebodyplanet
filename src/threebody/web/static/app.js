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

const scienceAutoRequested = new Set();

function showTaskProgress(info) {
  const physical = info.physical || {};
  const committed = physical.committed_segments || 0;
  const planned = physical.planned_segments || 0;
  const progress = document.querySelector('#physical-progress');
  progress.value = planned ? Math.min(100, Math.round(100 * committed / planned)) : 0;
  document.querySelector('#physical-phase').textContent =
    physical.status || info.phase || '尚未开始';
  document.querySelector('#physical-progress-text').textContent =
    '已提交物理分段 ' + committed + '/' + planned +
    ' · REBOUND时间 ' + (physical.simulation_time_s ?? '—') + ' SI秒' +
    (info.phase === 'RECOVERY_REQUIRED' ? ' · 检测到中断，请显式恢复' : '');
  const analysis = info.analysis_status || 'NOT_STARTED';
  document.querySelector('#analysis-phase').textContent = analysis;
  const details = info.analysis || {};
  const source = info.artifacts_present || {};
  let message = {
    NOT_STARTED: '尚未执行科学派生；已完成的旧运行可点击「重新验证/分析科学数据」。',
    QUEUED: '真实物理积分已结束，辐射、温度与M4科学分析已排队。',
    RUNNING: '正在运行M3辐射、C0/C1热积分及M4世界分类，请继续观察任务状态。',
    COMPLETED: '科学分析结束，可在下方重新读取轨道、辐射、温度与M4分类。',
    PARTIAL: '部分分析完成。' + (details.thermal || '有科学数据未计算'),
    FAILED: '科学分析失败：' + (info.analysis_error || '请查看诊断信息'),
    INTERRUPTED: '科学分析在服务重启前中断；请重新触发分析。',
    DEFERRED_SHUTDOWN: '关闭Web服务期间推迟分析；可手动重新运行。',
  }[analysis] || '当前科学派生状态：' + analysis;
  if (physical.status === 'COLLISION') {
    message += ' 碰撞终态不生成碰撞后的温度；M4动力学结局仍可计算。';
  }
  message += ' · 已保存归档：温度' + (source.thermal ? '有' : '无') +
    '，M4' + (source.outcome ? '有' : '无') +
    '（存在不代表已通过来源核验）。';
  document.querySelector('#analysis-progress-text').textContent = message;
}

function scienceMissingNotice(info, kind) {
  const physical = info?.physical?.status;
  const analysis = info?.analysis_status || 'NOT_STARTED';
  const title = kind === 'thermal' ? '辐射/C0/C1' : 'M4世界结局';
  if (kind === 'thermal' && physical === 'COLLISION') {
    return '碰撞终止：不推算碰撞后的辐射与温度；M4仍可分析动力学结局。';
  }
  if (['QUEUED', 'RUNNING'].includes(analysis)) {
    return title + '正在真实计算或等待分析任务，完成后自动刷新；不是数据丢失。';
  }
  if (analysis === 'PARTIAL') {
    return title + '暂不可用：' +
      (info.analysis?.thermal || '超出物理采样或积分计算预算');
  }
  if (analysis === 'FAILED') {
    return title + '分析失败：' +
      (info.analysis_error || '原因见「诊断信息」') +
      '；请检查物理归档后点击重新分析。';
  }
  if (physical === 'PAUSED' || physical === 'RUNNING') {
    return '物理积分尚未完成，目前不生成最终' + title + '。';
  }
  if (analysis === 'COMPLETED') {
    return title + '归档缺失，但任务报告分析完成；请重新验证/分析科学数据。';
  }
  return title + '尚未生成：可使用「重新验证/分析科学数据」补算。';
}

async function autoRecoverScienceIfNeeded(id, info) {
  const physical = info?.physical?.status;
  const status = info?.analysis_status;
  if (!['COMPLETED', 'COLLISION'].includes(physical)) return info;
  const existing = info?.artifacts_present || {};
  const missing = !existing.outcome || (physical === 'COMPLETED' && !existing.thermal);
  if (
    !missing || !['NOT_STARTED', 'INTERRUPTED', 'DEFERRED_SHUTDOWN'].includes(status)
    || scienceAutoRequested.has(id)
  ) return info;
  scienceAutoRequested.add(id);
  try {
    const queued = await apiPost('/api/v1/runs/' + encodeURIComponent(id) + '/analyze', {});
    showTaskProgress(queued);
    beginPoll(id);
    return queued;
  } catch(error) {
    document.querySelector('#analysis-progress-text').textContent =
      '自动补算未成功：' + error.message + '。可查看诊断并手动重新分析。';
    return info;
  }
}

async function pollJob() {
  if (!activeRunId) return;
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(activeRunId)}`);
    if (!response.ok) throw new Error('当前进程没有该任务的活动记录');
    const info = await response.json();
    jobDetails.textContent = JSON.stringify(info, null, 2);
    showTaskProgress(info);
    jobState.textContent = `任务 ${info.run_id}: ${info.phase} · 物理状态 ${info.physical?.status ?? 'PENDING'} · 科学派生 ${info.analysis_status ?? 'NOT_STARTED'}`;
    if (
      ['COMPLETED','COLLISION','FAILED','CANCELLED','CANCELLED_BEFORE_START','PAUSED','WORKER_ERROR'].includes(info.phase)
      && !['QUEUED','RUNNING'].includes(info.analysis_status)
    ) {
      clearInterval(pollHandle);
      pollHandle = null;
      loadRuns();
      if (
        ['COMPLETED', 'COLLISION'].includes(info.phase)
        && document.querySelector('#observe-run-id').value === info.run_id
      ) {
        loadOrbit();
      }
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
let linkedThermal = null;
let orbitTimer = null;
let orbitZoom = 1;
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
  const scale=extent/(Math.min(w,h)*.40)*orbitZoom;
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
  renderThermal();
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
    let scienceJob = null;
    try {
      const jobResponse = await fetch('/api/v1/jobs/' + encodeURIComponent(id));
      if (jobResponse.ok) {
        scienceJob = await jobResponse.json();
        showTaskProgress(scienceJob);
        scienceJob = await autoRecoverScienceIfNeeded(id, scienceJob);
      }
    } catch(error) {
      document.querySelector('#analysis-progress-text').textContent =
        '科学任务状态未能读取：' + error.message;
    }
    linkedThermal=null;
    try {
      const environmentResponse=await fetch(base+'/environment?'+suffix);
      if(environmentResponse.ok) {
        const environment=await environmentResponse.json();
        if(environment.world_id!==trajectory.world_id || environment.run_id!==id) {
          throw Error('派生环境数据的世界/运行ID与轨道不符');
        }
        linkedThermal=environment;
        document.querySelector('#environment-status').textContent=
          'M5A已验证派生数据 · '+environment.thermal_mode+
          ' · '+environment.sample_count+'物理点 · 显示'+environment.display_count+'点'+
          (environment.sampling_warning?
            ' · ⚠ 轨道最大采样间隔'+
            (environment.source_max_orbit_gap_s/86400).toFixed(2)+
            '天，温度为低置信度近似，无法还原采样之间的近距离经过及辐照峰值。'+
            '建议以后使用不超过1天的轨道输出间隔。':'');
      } else if(environmentResponse.status===404) {
        document.querySelector('#environment-status').textContent=
          scienceMissingNotice(scienceJob, 'thermal');
      } else {
        const detail=await environmentResponse.json();
        throw Error(detail.message||detail.error||'派生环境归档验证失败');
      }
    } catch(thermalError) {
      document.querySelector('#environment-status').textContent=
        '温度/辐射无法载入：'+thermalError.message;
      linkedThermal=null;
    }
    orbitSamples=trajectory.samples;
    orbitEventItems=events.events;
    try {
      const response=await fetch(base+'/outcome');
      if(response.ok){
        const outcome=await response.json();
        if(outcome.world_id!==trajectory.world_id || outcome.run_id!==id){
          throw Error('M4与当前轨道来源不同');
        }
        const report=outcome.classification;
        const labels=(report.environmental_outcomes||[]).join('、')||'无';
        document.querySelector('#m4-outcome-summary').textContent=
          '动力学：'+report.dynamical_outcome+
          ' · 行星状态：'+report.planet_status+
          ' · 环境：'+labels+
          ' · 来源：M5A '+outcome.source.m5a+
          ' / M3 '+outcome.source.thermal;
        orbitEventItems.push(...outcome.timeline
          .filter(e=>e.source==='M4')
          .map(e=>({
            event_type:e.event_type,body_ids:e.body_ids||[],
            t_s:e.t_start_s,message:e.message,source:'M4'
          })));
        orbitEventItems.sort((a,b)=>a.t_s-b.t_s);
      }else if(response.status===404){
        document.querySelector('#m4-outcome-summary').textContent=
          scienceMissingNotice(scienceJob, 'outcome');
      }else{
        const detail=await response.json();
        throw Error(detail.message||detail.error||'M4分类读取失败');
      }
    }catch(error){
      document.querySelector('#m4-outcome-summary').textContent=
        'M4结局不可用：'+error.message;
    }
    orbitIndex=0;orbitSlider.min=0;
    orbitSlider.max=Math.max(0,orbitSamples.length-1);
    orbitStatus.textContent='世界 '+trajectory.world_id+' · '+trajectory.sample_count+
      ' 条真实采样，显示 '+trajectory.display_count+' 条 · '+trajectory.data_status+
      ' · 无外推 · 事件 '+events.event_count;
    orbitEvents.replaceChildren(new Option('选择事件',''));
    orbitEventItems.forEach((event,i)=>{
      const collisionKind=event.observed?.collision_class;
      const displayType=collisionKind==='STAR_STAR'?'恒星碰撞':
        collisionKind==='STAR_PLANET'?'恒星—行星碰撞':event.event_type;
      orbitEvents.add(new Option(displayType+' @ '+event.t_s+' s',String(i)));
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
  const scenario=event.observed?.stellar_merger_scenario;
  const mergerSummary=scenario?
    ' · 恒星合并假设：质量 '+
    (scenario.hypothetical_merged_mass_kg/1.98847e30).toFixed(3)+
    ' M☉ · 相对动能 '+scenario.relative_kinetic_energy_available_j.toExponential(4)+
    ' J · 引力变化'+
    (scenario.planet_acceleration_change_at_contact_m_s2?'已估算':'不可估算')+
    (Number.isFinite(scenario.planet_bolometric_flux_proxy_change_w_m2)?
      ' · 假设松弛后辐照差 '+
      scenario.planet_bolometric_flux_proxy_change_w_m2.toExponential(4)+' W/m²':'')+
    ' · 仅诊断，无碰撞闪光或碰撞后轨道积分':'';
  document.querySelector('#orbit-event-detail').textContent=
    event.event_type+' ('+event.body_ids.join(', ')+') @ '+event.t_s+' s · '+
    event.message+' · 采样时间偏差 '+delta.toPrecision(5)+' s'+mergerSummary;
});
document.querySelector('#runs').addEventListener('click',event=>{
  const button=event.target.closest('[data-run]');
  if(!button)return;
  document.querySelector('#observe-run-id').value=button.dataset.run;
  document.querySelector('#run-id').value=button.dataset.run;
});
orbitDraw();

/* Derived environment shares authoritative M5A physical time, not display frame rate. */
function thermalNearestIndex(rows,t) {
  if(!rows.length)return -1;
  let lo=0,hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid].t_s<t)lo=mid+1;else hi=mid;}
  if(lo===0)return 0;if(lo===rows.length)return lo-1;
  return t-rows[lo-1].t_s<=rows[lo].t_s-t?lo-1:lo;
}
function blankThermal(canvas,text) {
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#080c0d';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#9baab0';ctx.font='14px monospace';ctx.fillText(text,20,30);
}
function drawTimeSeries(canvas,rows,extract,colors,title,selectedTime) {
  const ctx=canvas.getContext('2d'), w=canvas.width,h=canvas.height;
  ctx.fillStyle='#080c0d';ctx.fillRect(0,0,w,h);
  const tracks=colors.map((_,track)=>rows.map(point=>extract(point,track)));
  const values=tracks.flat().filter(v=>v!==null&&Number.isFinite(v));
  if(values.length===0){blankThermal(canvas,title+' — 数据未计算');return;}
  const t0=rows[0].t_s,t1=rows[rows.length-1].t_s;
  let low=Math.min(...values),high=Math.max(...values);
  if(high===low){const padding=Math.max(1,Math.abs(high)*.02);low-=padding;high+=padding;}
  const plot=(time,value)=>[48+((time-t0)/Math.max(t1-t0,1))*(w-70),
    26+(1-(value-low)/(high-low))*(h-64)];
  ctx.font='13px monospace';ctx.fillStyle='#d0e0e5';
  ctx.fillText(title+'  '+low.toPrecision(4)+' .. '+high.toPrecision(4),18,17);
  tracks.forEach((values,j)=>{
    ctx.strokeStyle=colors[j];ctx.lineWidth=j===tracks.length-1?2.5:1.4;
    let drawing=false;ctx.beginPath();
    values.forEach((v,i)=>{
      if(v===null||!Number.isFinite(v)){drawing=false;return;}
      const [x,y]=plot(rows[i].t_s,v);
      if(!drawing){ctx.moveTo(x,y);drawing=true;}else ctx.lineTo(x,y);
    });ctx.stroke();
  });
  const cursor=Math.max(48,Math.min(w-22,plot(selectedTime,low)[0]));
  ctx.strokeStyle='#e9f1f2';ctx.setLineDash([4,5]);ctx.beginPath();
  ctx.moveTo(cursor,24);ctx.lineTo(cursor,h-24);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#a1aeb3';ctx.fillText('t='+selectedTime.toPrecision(7)+' SI秒',w-240,h-8);
}
/* The C1 solver uses equal-area x=sin(latitude) cells, not equal-degree bands.
 * Draw the true latitude edges; retain full K precision even for a 60s experiment.
 */
const c1Plot = {left:48,top:30,right:920,bottom:207};
let visibleC1Rows = [];
function c1TemperatureText(k) {
  return k.toFixed(6) + ' K / ' + (k-273.15).toFixed(6) + ' °C';
}
function c1SummaryAtTime(rows,selectedTime) {
  const summary=document.querySelector('#c1-temperature-summary');
  const eligible=rows.filter(r=>Array.isArray(r.c1_temperature_k));
  if(!eligible.length){
    summary.textContent='C1纬度温差：没有可用的真实温度采样。';
    return;
  }
  const point=eligible[thermalNearestIndex(eligible,selectedTime)];
  const values=point.c1_temperature_k;
  if(!values.length||!values.every(Number.isFinite)){
    summary.textContent='C1纬度温差：数据无效或缺失。';
    return;
  }
  const min=Math.min(...values),max=Math.max(...values),delta=max-min;
  summary.textContent='当前真实C1采样 t='+point.t_s.toPrecision(9)+
    ' s · 最低 '+min.toFixed(6)+' K · 最高 '+max.toFixed(6)+
    ' K · 纬度温差 ΔT='+delta.toFixed(6)+' K'+
    (delta<0.01?'（温差不足0.01 K，视觉上接近一致）':'')+
    (point.t_s===0?' · 初始统一温度是模型设置，并非绘制错误':'');
}
function drawC1(rows,selectedTime) {
  const canvas=document.querySelector('#c1-canvas');
  const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
  ctx.fillStyle='#080c0d';ctx.fillRect(0,0,w,h);
  const columns=rows.filter(r=>Array.isArray(r.c1_temperature_k));
  visibleC1Rows=columns;
  c1SummaryAtTime(rows,selectedTime);
  if(!columns.length){
    blankThermal(canvas,'C1纬度温度未计算');
    return;
  }
  const bands=columns[0].c1_temperature_k.length;
  if(!bands||columns.some(r=>r.c1_temperature_k.length!==bands)){
    visibleC1Rows=[];
    blankThermal(canvas,'C1纬度温度数据不一致');
    return;
  }
  const values=columns.flatMap(r=>r.c1_temperature_k);
  const min=Math.min(...values),max=Math.max(...values),spread=max-min;
  if(!Number.isFinite(min)||!Number.isFinite(max)){
    visibleC1Rows=[];
    blankThermal(canvas,'C1纬度温度数值不可用');
    return;
  }
  ctx.fillStyle='#cfe0e3';ctx.font='13px monospace';
  ctx.fillText('C1纬度温度 (K) · 范围 '+min.toFixed(6)+' .. '+
    max.toFixed(6),16,17);
  if(spread<0.01){
    ctx.fillStyle='#a9bac0';ctx.font='12px monospace';
    ctx.fillText('温差小于0.01 K · 色阶仅显示相对变化',w-315,17);
  }
  const {left,top,right,bottom}=c1Plot;
  columns.forEach((col,i)=>{
    const x=left+i*(right-left)/columns.length;
    col.c1_temperature_k.forEach((temp,b)=>{
      // The physical finite-volume band boundaries are equal in sin(latitude).
      const low=Math.asin(-1+2*b/bands);
      const high=Math.asin(-1+2*(b+1)/bands);
      const y=top+(Math.PI/2-high)/Math.PI*(bottom-top);
      const yBottom=top+(Math.PI/2-low)/Math.PI*(bottom-top);
      const fraction=(temp-min)/Math.max(spread,1e-12);
      ctx.fillStyle='hsl('+(240-240*fraction).toFixed(1)+',85%,54%)';
      ctx.fillRect(x,y,Math.ceil((right-left)/columns.length)+1,
        Math.ceil(yBottom-y)+1);
    });
  });
  const t0=columns[0].t_s,t1=columns[columns.length-1].t_s;
  const cursor=left+(selectedTime-t0)/Math.max(t1-t0,1)*(right-left);
  ctx.strokeStyle='#fff';ctx.setLineDash([3,5]);ctx.beginPath();
  ctx.moveTo(Math.max(left,Math.min(right,cursor)),top);
  ctx.lineTo(Math.max(left,Math.min(right,cursor)),bottom);ctx.stroke();
  ctx.setLineDash([]);ctx.fillStyle='#aab8bd';
  ctx.fillText('−90°',5,bottom);ctx.fillText('+90°',5,top+10);
  ctx.fillText('时间 →',right-62,bottom+20);
}
const c1Canvas=document.querySelector('#c1-canvas');
c1Canvas.addEventListener('pointermove',event=>{
  const target=document.querySelector('#c1-temperature-hover');
  const rect=c1Canvas.getBoundingClientRect();
  const x=(event.clientX-rect.left)*c1Canvas.width/rect.width;
  const y=(event.clientY-rect.top)*c1Canvas.height/rect.height;
  const {left,top,right,bottom}=c1Plot;
  if(!visibleC1Rows.length||x<left||x>right||y<top||y>bottom){
    target.textContent='将鼠标移到热图上查看纬度、物理时间及温度（K / °C）。';
    return;
  }
  const sampleIndex=Math.min(visibleC1Rows.length-1,
    Math.floor((x-left)/(right-left)*visibleC1Rows.length));
  const sample=visibleC1Rows[sampleIndex];
  const latitude=Math.PI/2-(y-top)/(bottom-top)*Math.PI;
  const bands=sample.c1_temperature_k.length;
  const band=Math.max(0,Math.min(bands-1,
    Math.floor((Math.sin(latitude)+1)*bands/2)));
  const center=sample.c1_latitude_rad[band]*180/Math.PI;
  const kelvin=sample.c1_temperature_k[band];
  target.textContent='实际温度采样 t='+sample.t_s.toPrecision(9)+
    ' s · 纬度带中心 '+center.toFixed(2)+'° · '+
    c1TemperatureText(kelvin)+'（无插值）';
});
c1Canvas.addEventListener('pointerleave',()=>{
  document.querySelector('#c1-temperature-hover').textContent=
    '将鼠标移到热图上查看纬度、物理时间及温度（K / °C）。';
});
function updateCurrentC0Reading(row, selectedTime) {
  const kelvin = document.querySelector('#c0-current-k');
  const celsius = document.querySelector('#c0-current-celsius');
  const timeLabel = document.querySelector('#c0-current-time');
  const temp = row?.c0_temperature_k;
  if (typeof temp !== 'number' || !Number.isFinite(temp) || temp < 0) {
    kelvin.textContent = '— K';
    celsius.textContent = '— °C';
    timeLabel.textContent = '该位置没有可用的C0全球温度数据';
    return;
  }
  const sourceTime = row.t_s;
  kelvin.textContent = temp.toFixed(2) + ' K';
  celsius.textContent = (temp - 273.15).toFixed(2) + ' °C';
  timeLabel.textContent = '真实热模型采样 t=' + sourceTime.toPrecision(9)
    + ' SI秒 · 与当前轨道时间相差 '
    + Math.abs(selectedTime - sourceTime).toPrecision(4)
    + ' 秒（使用最近采样，不插值）';
}
function renderThermal(){
  const flux=document.querySelector('#flux-canvas');
  const c0=document.querySelector('#c0-canvas');
  const cursor=document.querySelector('#thermal-cursor');
  const c1=document.querySelector('#c1-canvas');
  if(!linkedThermal||!linkedThermal.rows.length||!orbitSamples.length){
    blankThermal(flux,'恒星辐照度：无可信数据');
    blankThermal(c0,'C0：无可信数据');
    blankThermal(c1,'C1：无可信数据');
    cursor.textContent='轨道时间轴与热模型数据尚未建立有效关联。';
    updateCurrentC0Reading(null, 0);
    return;
  }
  const t=orbitSamples[orbitIndex].t_s,rows=linkedThermal.rows;
  const index=thermalNearestIndex(rows,t),nearest=rows[index];
  updateCurrentC0Reading(nearest,t);
  drawTimeSeries(flux,rows,(p,i)=>p.flux_w_m2[
    ['star-1','star-2','star-3','total'][i]],
    ['#f3a44a','#e27459','#8bb7ff','#fff'],
    '恒星辐照度 W/m²（星1/星2/星3/总计）',t);
  drawTimeSeries(c0,rows,p=>p.c0_temperature_k,
    ['#edb86c'],'C0 全球温度 K',t);
  drawC1(rows,t);
  cursor.textContent='轨道t='+t.toPrecision(9)+'s · 环境最近真实采样t='+
    nearest.t_s.toPrecision(9)+'s · 时间差='+
    Math.abs(t-nearest.t_s).toPrecision(4)+'s · 未推算缺失时间';
}
renderThermal();

/* Editable, reproducible G2.1 candidate generation and real IAS15 world launch. */
let worldPreset=null, worldBatch=null, worldChoice=null;
const worldStatus=document.querySelector('#world-generator-status');
const worldGallery=document.querySelector('#world-candidates');
const worldStart=document.querySelector('#world-start');
const worldStartStatus=document.querySelector('#world-start-status');
async function loadWorldPreset(){
  try {
    const response=await fetch('/api/v1/worlds/preset');
    if(!response.ok)throw Error('服务端未提供默认生成参数');
    worldPreset=await response.json();
    document.querySelector('#world-advanced-config').value=JSON.stringify(worldPreset,null,2);
    worldStatus.textContent='G2.1科学配置已加载；随机生成仅设置初态，不预设演化结局。';
  } catch(error){worldStatus.textContent='配置载入失败：'+error.message;}
}
function worldConfiguration(){
  const text=document.querySelector('#world-advanced-config').value;
  const config=JSON.parse(text);
  const seed=Number(document.querySelector('#world-seed').value);
  const count=Number(document.querySelector('#world-count').value);
  const mode=document.querySelector('#world-mode').value;
  if(!Number.isSafeInteger(seed)||seed<0||seed>4294967295)throw Error('随机种子不合法');
  if(!Number.isSafeInteger(count)||count<1||count>20)throw Error('候选数量必须在1~20');
  const constraints=config.world_generation.generator.initial_constraints;
  const speed=Number(document.querySelector('#world-max-speed').value);
  const distance=Number(document.querySelector('#world-max-distance').value);
  const escapeRatio=Number(document.querySelector('#world-escape-ratio').value);
  const eccentricity=Number(document.querySelector('#world-eccentricity').value);
  if(![speed,distance,escapeRatio,eccentricity].every(Number.isFinite)) {
    throw Error('物理参数必须是有限数值');
  }
  config.experiment.seed=seed;config.experiment.candidate_count=count;
  constraints.mode=mode;
  constraints.planet_speed.absolute_max_mps=speed*1000;
  constraints.planet_star_distance_m.max_nearest=distance*149597870700;
  constraints.planet_speed.escape_speed_fraction_max=escapeRatio;
  config.world_generation.generator.geometry.planet.eccentricity.max=eccentricity;
  return {config,count};
}
function renderWorldGallery(){
  worldGallery.replaceChildren();
  if(!worldBatch)return;
  for(const candidate of worldBatch.candidates){
    const button=document.createElement('button');
    button.type='button';button.className='world-card';
    button.dataset.index=String(candidate.index);
    button.disabled=!candidate.can_integrate;
    if(worldChoice===candidate.index)button.classList.add('selected');
    const label=document.createElement('strong');
    label.textContent=candidate.world_id;
    const status=document.createElement('span');
    status.textContent=candidate.can_integrate?'可积分 · 可选择':
      candidate.initial_contact?'初始接触 · 仅归档':
      candidate.generation_error?'生成失败 · '+candidate.generation_error:
      '初态物理检查未通过';
    button.append(label,status);
    if(candidate.can_integrate){
      button.addEventListener('click',()=>{
        worldChoice=candidate.index;
        worldStart.disabled=false;
        worldStartStatus.textContent='选中'+candidate.world_id+
          ' · 将直接使用已归档初态运行真实IAS15';
        renderWorldGallery();
      });
    }
    worldGallery.append(button);
  }
}
document.querySelector('#world-generate').addEventListener('click',async()=>{
  try {
    const payload=worldConfiguration();
    worldStatus.textContent='生成真实随机初态中…';
    worldChoice=null;worldBatch=null;worldStart.disabled=true;
    const result=await apiPost('/api/v1/worldsets',payload);
    worldBatch=result;renderWorldGallery();
    worldStatus.textContent='生成完成 · seed='+result.seed+' · '+result.count+
      '个候选 · 可选择'+result.candidates.filter(c=>c.can_integrate).length+'个';
  } catch(error){worldStatus.textContent='生成失败：'+error.message;}
});
document.querySelector('#world-start').addEventListener('click',async()=>{
  if(!worldBatch||worldChoice===null)return;
  try {
    const id=document.querySelector('#world-run-id').value;
    const duration_s=Number(document.querySelector('#world-duration').value);
    const output_interval_s=Number(document.querySelector('#world-sampling').value);
    worldStartStatus.textContent='提交到真实IAS15后台…';
    const result=await apiPost('/api/v1/worlds/run',{
      batch_id:worldBatch.batch_id,candidate_index:worldChoice,
      run_id:id,duration_s,output_interval_s
    });
    worldStartStatus.textContent='运行请求已接受；状态以服务端归档为准。';
    document.querySelector('#run-id').value=id;
    document.querySelector('#observe-run-id').value=id;
    beginPoll(result.run_id);
  }catch(error){worldStartStatus.textContent='启动失败：'+error.message;}
});
loadWorldPreset();

/* 3D scientific orbit camera: drag rotates inertial Cartesian view; wheel zooms. */
orbitCanvas.style.touchAction='none';
let dragPoint=null;
orbitCanvas.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;
  dragPoint=[event.clientX,event.clientY];
  orbitCanvas.setPointerCapture(event.pointerId);
});
orbitCanvas.addEventListener('pointermove',event=>{
  if(!dragPoint)return;
  const dx=event.clientX-dragPoint[0],dy=event.clientY-dragPoint[1];
  dragPoint=[event.clientX,event.clientY];
  orbitAzimuth+=dx*.008;
  orbitAltitude=Math.max(-1.55,Math.min(1.55,orbitAltitude+dy*.008));
  orbitDraw();
});
for(const kind of ['pointerup','pointercancel','lostpointercapture']){
  orbitCanvas.addEventListener(kind,()=>{dragPoint=null;});
}
orbitCanvas.addEventListener('wheel',event=>{
  event.preventDefault();
  orbitZoom=Math.max(.12,Math.min(15,orbitZoom*Math.exp(event.deltaY*.001)));
  orbitDraw();
},{passive:false});

/* Bounded immutable science comparison, explicitly displaying missing evidence. */
function selectedComparisonIds() {
  const ids=document.querySelector('#comparison-run-ids').value.split(',')
    .map(s=>s.trim()).filter(Boolean);
  if(!ids.length||ids.length>8||new Set(ids).size!==ids.length) {
    throw Error('请填写1~8个不重复的运行ID');
  }
  return ids;
}
async function fetchScienceComparison(report=false) {
  const ids=selectedComparisonIds();
  const path='/api/v1/experiments/'+(report?'report':'compare')+
    '?runs='+encodeURIComponent(ids.join(','));
  const response=await fetch(path);
  const data=await response.json();
  if(!response.ok)throw Error(data.message||data.error||'科学归档验证失败');
  return data;
}
document.querySelector('#compare-runs').addEventListener('click',async()=>{
  const status=document.querySelector('#comparison-status');
  const results=document.querySelector('#comparison-results');
  status.textContent='校验M5A/M3/M4来源哈希及物理归档中…';
  results.replaceChildren();
  try {
    const report=await fetchScienceComparison();
    const table=document.createElement('table');
    const header=document.createElement('tr');
    for(const h of ['运行ID','世界ID','动力学状态','物理时间(s)','样本数','环境/结局']){
      const th=document.createElement('th');th.textContent=h;header.append(th);
    }
    table.append(header);
    for(const entry of report.runs){
      const tr=document.createElement('tr');
      for(const text of [
        entry.run_id, entry.world_id, entry.status,
        String(entry.simulation_time_s), String(entry.trajectory_samples),
        entry.classification?.environmental_outcomes?.join('、')||
          entry.science_data_status+'（温度/分类可能缺失）'
      ]) {
        const td=document.createElement('td');td.textContent=text;tr.append(td);
      }
      table.append(tr);
    }
    results.append(table);
    status.textContent='已验证'+report.runs.length+
      '个真实归档 · 来源摘要 '+report.comparison_sha256.slice(0,16)+'…';
  } catch(error){status.textContent='比较失败：'+error.message;}
});
document.querySelector('#download-report').addEventListener('click',async()=>{
  const status=document.querySelector('#comparison-status');
  try {
    const report=await fetchScienceComparison(true);
    const payload=JSON.stringify(report,null,2);
    const blob=new Blob([payload],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    try {
      const link=document.createElement('a');
      link.href=url;link.download='threebody-science-report.json';
      document.body.append(link);link.click();link.remove();
    } finally {URL.revokeObjectURL(url);}
    status.textContent='科学报告已由浏览器发起保存；仅含已验证归档与显式缺失字段。';
  }catch(error){status.textContent='报告生成失败：'+error.message;}
});

/* Explicit, authoritative recovery after a Web service restart. */
document.querySelector('#restore-run').addEventListener('click',()=>{
  const id=document.querySelector('#run-id').value.trim();
  if(!id){jobState.textContent='请填写持久运行ID';return;}
  beginPoll(id);
});
document.querySelector('#analyze-run').addEventListener('click',async()=>{
  const id=document.querySelector('#run-id').value.trim();
  try {
    const result=await apiPost(
      '/api/v1/runs/'+encodeURIComponent(id)+'/analyze',{}
    );
    jobState.textContent='科学分析请求已接受；实际结果需等服务器核验完来源链。';
    beginPoll(result.run_id);
  }catch(error){jobState.textContent='科学重试失败：'+error.message;}
});
