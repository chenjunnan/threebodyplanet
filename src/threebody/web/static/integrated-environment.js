(function(){
  'use strict';
  const byId=id=>document.getElementById(id);
  const canvas=byId('integrated-environment-map');
  const context=canvas.getContext('2d');
  const status=byId('integrated-environment-status');
  const point=byId('integrated-environment-point');
  const time=byId('environment-time');
  let tile=null;
  let timer=null;

  const finite=value=>Number.isFinite(value)?value:null;
  function color(value,min,max,layer){
    if(value===null)return '#1a2028';
    const span=Math.max(max-min,1e-30);
    const x=Math.max(0,Math.min(1,(value-min)/span));
    if(layer==='liquid_water_kg_m2')return `rgb(${20+20*x},${70+80*x},${100+155*x})`;
    if(layer==='ice_kg_m2')return `rgb(${100+150*x},${140+110*x},${170+85*x})`;
    if(layer==='magma_fraction')return `rgb(${60+195*x},${20+90*x},${12+20*x})`;
    if(layer==='precipitation_rate_kg_m2_s')return `rgb(${30+80*x},${35+100*x},${60+195*x})`;
    if(layer==='relative_humidity')return `rgb(${45+60*x},${55+130*x},${70+170*x})`;
    const hue=240-240*x;
    return `hsl(${hue} 78% 52%)`;
  }
  function draw(){
    context.clearRect(0,0,canvas.width,canvas.height);
    if(!tile)return;
    const layer=byId('environment-layer').value;
    const values=tile.cells.map(cell=>finite(cell.fields[layer])).filter(value=>value!==null);
    const min=Math.min(...values),max=Math.max(...values);
    const rows=tile.grid[0],columns=tile.grid[1];
    const width=canvas.width/columns,height=canvas.height/rows;
    for(const cell of tile.cells){
      const row=Math.floor(cell.tile_cell_index/columns);
      const column=cell.tile_cell_index%columns;
      context.fillStyle=color(finite(cell.fields[layer]),min,max,layer);
      context.fillRect(column*width,(rows-1-row)*height,Math.ceil(width),Math.ceil(height));
      if(cell.active_events.length){
        context.strokeStyle='#fff';context.lineWidth=2;
        context.strokeRect(column*width+1,(rows-1-row)*height+1,width-2,height-2);
      }
    }
    context.fillStyle='#fff';context.font='14px system-ui';
    context.fillText(`${layer}: ${min.toPrecision(5)} — ${max.toPrecision(5)} ${tile.units[layer]||''}`,12,22);
  }
  async function loadSeries(latitude,longitude){
    const run=byId('environment-run-id').value;
    const params=new URLSearchParams({latitude_deg:String(latitude),longitude_deg:String(longitude),max_points:'500'});
    const response=await fetch(`/api/v1/environment/${encodeURIComponent(run)}/series?${params}`);
    const data=await response.json();
    if(!response.ok)throw Error(data.message||data.error);
    const events=byId('integrated-environment-events');events.replaceChildren();
    const heading=document.createElement('strong');heading.textContent=`天气事件（${data.events.length}）`;events.append(heading);
    for(const event of data.events){
      const row=document.createElement('p');
      row.textContent=`${event.event_type} · ${event.start_t_s}—${event.end_t_s??'持续中'} s · ${event.confidence}`;
      events.append(row);
    }
  }
  async function queryPoint(cell){
    const run=byId('environment-run-id').value;
    const params=new URLSearchParams({t_s:String(tile.t_s),latitude_deg:String(cell.latitude_deg),longitude_deg:String(cell.longitude_deg)});
    const response=await fetch(`/api/v1/environment/${encodeURIComponent(run)}/point?${params}`);
    const data=await response.json();if(!response.ok)throw Error(data.message||data.error);
    const f=data.fields,w=data.weather;
    point.textContent=`${data.query.latitude_deg.toFixed(2)}°, ${data.query.longitude_deg.toFixed(2)}° · 地表 ${f.surface_temperature.value.toFixed(2)} K · 空气 ${f.air_temperature.value===null?'不可用':f.air_temperature.value.toFixed(2)+' K'} · 压强 ${f.surface_pressure.value.toPrecision(5)} Pa · 湿度 ${f.relative_humidity.value===null?'不可用':(100*f.relative_humidity.value).toFixed(1)+'%'} · 风 ${f.eastward_wind.value===null?'不可用':Math.hypot(f.eastward_wind.value,f.northward_wind.value).toFixed(2)+' m/s'} · ${w.state} · 质量 ${w.quality}`;
    await loadSeries(cell.latitude_deg,cell.longitude_deg);
  }
  async function loadTile(index=Number(time.value)){
    const run=byId('environment-run-id').value;
    const level=byId('environment-level').value;
    status.textContent='正在校验并读取不可变环境瓦片…';
    const response=await fetch(`/api/v1/environment/${encodeURIComponent(run)}/tiles/${index}/${level}`);
    const data=await response.json();if(!response.ok)throw Error(data.message||data.error);
    tile=data;time.max=String(data.time_index_range[1]);time.value=String(data.time_index);
    byId('environment-time-label').textContent=`t=${data.t_s} SI s · 索引 ${data.time_index}`;
    status.textContent=`${data.model_id} · ${data.fidelity_mode} · ${data.grid.join('×')}显示网格 · 同源结果 ${data.result_sha256.slice(0,12)}…`;
    byId('integrated-environment-provenance').textContent=JSON.stringify({model_id:data.model_id,coverage_s:data.coverage_s,config_sha256:data.config_sha256,forcing_source_sha256:data.forcing_source_sha256,result_sha256:data.result_sha256,aggregation:data.aggregation},null,2);
    draw();
  }
  async function safely(action){try{await action();}catch(error){status.textContent='动态环境无法载入：'+error.message;}}
  byId('environment-load').addEventListener('click',()=>safely(()=>loadTile(0)));
  byId('environment-layer').addEventListener('change',draw);
  byId('environment-level').addEventListener('change',()=>safely(()=>loadTile()));
  time.addEventListener('input',()=>safely(()=>loadTile()));
  byId('environment-previous').addEventListener('click',()=>safely(()=>loadTile(Math.max(0,Number(time.value)-1))));
  byId('environment-next').addEventListener('click',()=>safely(()=>loadTile(Math.min(Number(time.max),Number(time.value)+1))));
  byId('environment-play').addEventListener('click',()=>{if(timer)return;timer=setInterval(()=>{const next=Number(time.value)+1;if(next>Number(time.max)){clearInterval(timer);timer=null;return;}safely(()=>loadTile(next));},600);});
  byId('environment-pause').addEventListener('click',()=>{clearInterval(timer);timer=null;});
  canvas.addEventListener('click',event=>{if(!tile)return;const bounds=canvas.getBoundingClientRect();const column=Math.min(tile.grid[1]-1,Math.floor((event.clientX-bounds.left)/bounds.width*tile.grid[1]));const screenRow=Math.min(tile.grid[0]-1,Math.floor((event.clientY-bounds.top)/bounds.height*tile.grid[0]));const row=tile.grid[0]-1-screenRow;const cell=tile.cells[row*tile.grid[1]+column];safely(()=>queryPoint(cell));});
  window.addEventListener('threebody-route',event=>{if(event.detail!=='environment'){clearInterval(timer);timer=null;}});
})();
