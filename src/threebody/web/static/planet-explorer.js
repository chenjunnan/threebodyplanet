(function(){
  'use strict';
  const map=document.querySelector('#planet-map'),ctx=map.getContext('2d');
  const status=document.querySelector('#planet-status');
  let scene=null,requestSerial=0,ground=null;
  try{ground=window.createPlanetGround(document.querySelector('#planet-ground'));}
  catch(error){status.textContent='地表WebGL不可用：'+error.message;}
  function clear(){
    scene=null;ctx.clearRect(0,0,map.width,map.height);ground?.load(null);
    document.querySelector('#planet-export').disabled=true;
    for(const id of ['planet-summary','planet-region','planet-sky','planet-limitations'])document.getElementById(id).textContent='';
  }
  function show(result){
    scene=result;const summary=document.querySelector('#planet-summary');summary.replaceChildren();
    for(const [label,value,unit] of [
      ['重力',scene.gravity_m_s2,'m/s²'],['赤道有效重力',scene.equatorial_effective_gravity_m_s2,'m/s²'],['假设大气表面压力',scene.pressure_pa,'Pa'],
      ['等温尺度高度',scene.scale_height_m,'m'],['假设氧分压',scene.oxygen_partial_pressure_pa,'Pa'],
      ['假设水蒸气分压',scene.water_vapor_partial_pressure_pa,'Pa'],
      ['赤道平衡温度',scene.equator_temperature_k,'K'],
      ['北极平衡温度',scene.north_pole_temperature_k,'K'],['南极平衡温度',scene.south_pole_temperature_k,'K']]){
      const p=document.createElement('p');p.textContent=label+'：'+(value==null?'未知':value.toPrecision(6))+' '+unit;summary.append(p);
    }
    const image=ctx.createImageData(map.width,map.height);
    for(let y=0;y<map.height;y++){
      const latitude=Math.asin(1-2*(y+.5)/map.height);
      const row=Math.min(23,Math.floor((1+Math.sin(latitude))*12));
      for(let x=0;x<map.width;x++){
        const longitude=-Math.PI+2*Math.PI*(x+.5)/map.width;
        const height=window.planetSurfaceHeight(scene,latitude,longitude);
        const region=scene.regions[row*48+Math.min(47,Math.floor(x/map.width*48))];
        const wet=height<scene.sea_level_m&&['ice','liquid_possible'].includes(region.potential_water_phase);
        const h=Math.max(0,Math.min(1,(height/scene.parameters.relief_m+1)/2));
        const color=wet?(region.potential_water_phase==='ice'?[210,230,239]:
          [15,65+50*h,95+70*h]):[60+150*h,72+120*h,40+105*h];
        const offset=(y*map.width+x)*4;
        image.data.set([...color.map(Math.round),255],offset);
      }
    }
    ctx.putImageData(image,0,0);
    document.querySelector('#planet-sky').textContent=scene.sky.map(s=>s.id+' 方位 '+
      s.azimuth_deg.toFixed(2)+'° / 高度 '+s.altitude_deg.toFixed(2)+'° '+
      (s.above_geometric_horizon?'地平线上方':'地平线下方')).join(' · ')+
      (ground?'':' · WebGL2不可用，保留地图与数值');
    document.querySelector('#planet-limitations').textContent='参数场景，不是地质/气候预测。'+scene.limitations.join(' ');
    ground?.load(scene);document.querySelector('#planet-export').disabled=false;
    status.textContent='场景 '+scene.scenario_sha256.slice(0,12)+' · '+scene.run_id+
      ' · 真实源采样 t='+scene.source.t_s+' SI s · 固定灰色平衡，不替代M3历史';
  }
  async function build(){
    const serial=++requestSerial;clear();
    try{
      if(!orbitSamples.length||!orbitLoadedRunId||document.querySelector('#observe-run-id').value!==orbitLoadedRunId)throw Error('请先读取真实轨道采样');
      const parameters={t_s:orbitSamples[orbitIndex].t_s};
      for(const input of document.querySelectorAll('[data-planet]')){
        if(input.value!==''){
          const value=Number(input.value);if(!Number.isFinite(value))throw Error('参数必须为有限数值');
          parameters[input.dataset.planet]=value;
        }
      }
      const axis=document.querySelector('#planet-axis').value;
      if(axis.trim())parameters.rotation_axis=JSON.parse(axis);
      const definition=document.querySelector('#planet-hazard-definition').value.trim();
      if(definition)parameters.hazard_definition=definition;
      parameters.hazard_rates_per_year=JSON.parse(document.querySelector('#planet-hazard-rates').value);
      status.textContent='验证归档并计算参数化场景…';
      const result=await apiPost('/api/v1/planet-scenarios',{run_id:orbitLoadedRunId,parameters});
      if(serial!==requestSerial)return;show(result);
    }catch(error){if(serial===requestSerial)status.textContent='无法计算：'+error.message;}
  }
  document.querySelector('#planet-build').addEventListener('click',build);
  map.addEventListener('click',e=>{
    if(!scene)return;
    const rect=map.getBoundingClientRect();
    const c=Math.max(0,Math.min(47,Math.floor((e.clientX-rect.left)/rect.width*48)));
    const r=23-Math.max(0,Math.min(23,Math.floor((e.clientY-rect.top)/rect.height*24)));
    const region=scene.regions[r*48+c];
    document.querySelector('#planet-region').textContent=region.id+' · '+region.latitude_deg.toFixed(2)+'° / '+
      region.longitude_deg.toFixed(2)+'° · 海拔 '+region.elevation_m.toFixed(1)+' m · 水深 '+
      region.water_depth_m.toFixed(1)+' m · 水相代理 '+region.water_phase_proxy+' · '+region.temperature_k.toFixed(2)+' K · 地转风尺度 '+
      (region.geostrophic_scale_mps==null?'不适用':region.geostrophic_scale_mps.toFixed(2)+' m/s')+
      ' · 区域灾害条件概率 '+(region.conditional_hazard_probability==null?'未知（未提供率）':
        (100*region.conditional_hazard_probability).toFixed(3)+'%');
    document.querySelector('[data-planet="latitude_deg"]').value=region.latitude_deg;
    document.querySelector('[data-planet="longitude_deg"]').value=region.longitude_deg;
    document.querySelector('#planet-export').disabled=true;ground?.load(null);
    document.querySelector('#planet-sky').textContent='地点已改变，请重新计算此地点的天空。';
    status.textContent='已选 '+region.id+'；点击计算以更新此地点的恒星天空。';
  });
  document.querySelector('#planet-export').addEventListener('click',()=>{
    if(!scene)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(scene,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='planet-exploration.json';link.click();URL.revokeObjectURL(url);
  });
  const invalidate=()=>{++requestSerial;clear();status.textContent='采样或参数已更改，请重新计算；旧场景已清空。';};
  window.addEventListener('threebody-time',invalidate);
  document.querySelector('#planet-parameters').addEventListener('input',invalidate);
  document.querySelector('#planet-hazard-rates').addEventListener('input',invalidate);
  document.querySelector('#planet-hazard-definition').addEventListener('input',invalidate);
  document.querySelector('#planet-axis').addEventListener('input',invalidate);
  document.querySelector('#observe-run-id').addEventListener('input',invalidate);
})();
