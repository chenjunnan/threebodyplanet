(function(){
  'use strict';
  const map=document.querySelector('#planet-map'),ctx=map.getContext('2d');
  const status=document.querySelector('#planet-status');
  const lockSelect=document.querySelector('#planet-lock-star');
  let scene=null,requestSerial=0,ground=null,globe=null,playback=null,rebuildTimer=null;
  try{ground=window.createPlanetGround(document.querySelector('#planet-ground'));}
  catch(error){status.textContent='地表WebGL不可用：'+error.message;}
  try{globe=window.createPlanetGlobe(document.querySelector('#planet-globe'));}
  catch(error){status.textContent+=' · 行星球体WebGL不可用：'+error.message;}

  function clear(){
    scene=null;window.currentPlanetExplorationScene=null;
    ctx.clearRect(0,0,map.width,map.height);ground?.load(null);globe?.load(null);
    document.querySelector('#planet-export').disabled=true;
    document.querySelector('#planet-goto-mountain').disabled=true;
    document.querySelector('#planet-goto-plain').disabled=true;
    for(const id of ['planet-summary','planet-region','planet-sky','planet-limitations',
      'planet-atmosphere-composition']){
      document.getElementById(id).textContent='';
    }
  }
  function drawMap(){
    // The continuous v6 morphology is expensive enough that evaluating every
    // display pixel would stall lower-end browsers. Half-resolution equal-area
    // sampling still exceeds the 96x192 scientific basin grid and is upscaled
    // only for display; click coordinates and the observer marker remain exact.
    const width=Math.ceil(map.width/2),height=Math.ceil(map.height/2);
    const raster=document.createElement('canvas');raster.width=width;raster.height=height;
    const rasterContext=raster.getContext('2d'),image=rasterContext.createImageData(width,height);
    const elevations=new Float32Array(width*height);
    let minimum=Infinity,maximum=-Infinity;
    for(let y=0;y<height;y++){
      const latitude=Math.asin(1-2*(y+.5)/height);
      const row=Math.min(23,Math.floor((1+Math.sin(latitude))*12));
      for(let x=0;x<width;x++){
        const longitude=-Math.PI+2*Math.PI*(x+.5)/width;
        const elevation=window.planetSurfaceHeight(scene,latitude,longitude);
        elevations[y*width+x]=elevation;minimum=Math.min(minimum,elevation);
        maximum=Math.max(maximum,elevation);
        const region=scene.regions[row*48+Math.min(47,Math.floor(x/width*48))];
        const wet=elevation<scene.sea_level_m&&
          ['ice','liquid_possible'].includes(region.potential_water_phase);
        const h=Math.max(0,Math.min(1,(elevation/scene.parameters.relief_m+1)/2));
        const color=wet?(region.potential_water_phase==='ice'?[210,230,239]:
          [15,65+50*h,95+70*h]):[48+180*h,58+145*h,30+125*h];
        const offset=(y*width+x)*4;image.data.set([...color.map(Math.round),255],offset);
      }
    }
    const count=scene.contour_levels_m.length,span=Math.max(1,maximum-minimum);
    const band=value=>Math.max(0,Math.min(count,Math.floor((value-minimum)/span*(count+1))));
    for(let y=0;y<height-1;y++)for(let x=0;x<width-1;x++){
      const index=y*width+x,b=band(elevations[index]);
      if(b!==band(elevations[index+1])||b!==band(elevations[index+width])){
        const offset=index*4;
        image.data[offset]=Math.round(image.data[offset]*.32);
        image.data[offset+1]=Math.round(image.data[offset+1]*.32);
        image.data[offset+2]=Math.round(image.data[offset+2]*.32);
      }
    }
    rasterContext.putImageData(image,0,0);ctx.imageSmoothingEnabled=true;
    ctx.clearRect(0,0,map.width,map.height);ctx.drawImage(raster,0,0,map.width,map.height);
    // The marker is the currently calculated observer, not a guessed click point.
    const marker=scene.observer_location;
    const markerX=(marker.longitude_deg+180)/360*map.width;
    const markerY=(1-Math.sin(marker.latitude_deg*Math.PI/180))/2*map.height;
    ctx.save();ctx.strokeStyle='#ffef5a';ctx.fillStyle='#172033';ctx.lineWidth=2;
    ctx.beginPath();ctx.arc(markerX,markerY,6,0,2*Math.PI);ctx.fill();ctx.stroke();
    ctx.beginPath();ctx.moveTo(markerX-10,markerY);ctx.lineTo(markerX+10,markerY);
    ctx.moveTo(markerX,markerY-10);ctx.lineTo(markerX,markerY+10);ctx.stroke();ctx.restore();
  }
  function show(result,{redrawMap=true}={}){
    scene=result;const summary=document.querySelector('#planet-summary');summary.replaceChildren();
    for(const [label,value,unit] of [
      ['重力',scene.gravity_m_s2,'m/s²'],['赤道有效重力',scene.equatorial_effective_gravity_m_s2,'m/s²'],
      ['参数大气表面压力',scene.pressure_pa,'Pa'],['等温尺度高度',scene.scale_height_m,'m'],
      ['99.9%柱质量有效厚度',scene.atmospheric_optics.effective_thickness_99_9_percent_m,'m'],
      ['表面分子数密度',scene.atmospheric_optics.surface_number_density_m3,'m⁻³'],
      ['假设氧分压',scene.oxygen_partial_pressure_pa,'Pa'],['假设水蒸气分压',scene.water_vapor_partial_pressure_pa,'Pa'],
      ['假设二氧化碳分压',scene.carbon_dioxide_partial_pressure_pa,'Pa'],
      ['赤道平衡温度',scene.equator_temperature_k,'K'],['北极平衡温度',scene.north_pole_temperature_k,'K'],
      ['南极平衡温度',scene.south_pole_temperature_k,'K']]){
      const p=document.createElement('p');p.textContent=label+'：'+
        (value==null?'未知':value.toPrecision(6))+' '+unit;summary.append(p);
    }
    const water=document.createElement('p');water.textContent='水库存状态：'+
      scene.water_inventory.state+' · 地表 '+
      scene.water_inventory.remaining_surface_water_kg.toPrecision(5)+' kg · 转入大气 '+
      scene.water_inventory.transferred_to_atmosphere_kg.toPrecision(5)+' kg';
    summary.append(water);
    if(scene.terrain_model){
      const terrain=document.createElement('p'),drivers=scene.terrain_model.drivers;
      terrain.textContent='环境地貌代理：平原 '+scene.terrain_model.plains.length+
        ' · 山脊 '+scene.terrain_model.ridges.length+' · 断裂峡谷/陡崖 '+
        scene.terrain_model.faults.length+' · 侵蚀指数 '+drivers.erosion_index.toFixed(3)+
        ' · 断裂指数 '+drivers.fracture_index.toFixed(3)+'（参数化先验，非板块反演）';
      summary.append(terrain);
    }
    if(scene.atmospheric_optics.validity.startsWith('QUALITATIVE_ONLY')){
      const warning=document.createElement('p');warning.className='warning';
      warning.textContent='高压大气警告：当前三波段单路径消光只提供定性结果；未求解多次散射和真实气体。几何可见的恒星可能没有可显示的直达星盘。';
      summary.append(warning);
    }
    const gasNames={nitrogen:'氮气',oxygen:'氧气',carbon_dioxide:'二氧化碳',
      nitrogen_dioxide:'二氧化氮',methane:'甲烷',sulfur_dioxide:'二氧化硫',
      water_vapor:'水蒸气',hydrogen:'氢气',helium:'氦气',ammonia:'氨气',argon:'氩气'};
    const composition=document.querySelector('#planet-atmosphere-composition');
    composition.replaceChildren();const heading=document.createElement('h4');
    heading.textContent='大气成分与垂直光学柱';composition.append(heading);
    const list=document.createElement('ul');
    for(const [gas,fraction] of Object.entries(scene.atmospheric_composition_mole_fraction)){
      const item=document.createElement('li'),diagnostic=
        scene.atmospheric_optics.gas_optical_contributions?.[gas];
      item.textContent=(gasNames[gas]||gas)+'：'+(fraction*100).toPrecision(5)+'%'+
        (diagnostic?' · 柱分子 '+diagnostic.column_molecules_m2.toExponential(3)+' m⁻²':'');
      list.append(item);
    }
    composition.append(list);
    if(redrawMap)drawMap();
    const visibilityLabel=s=>s.direct_disc_visibility==='VISIBLE_DIRECT_DISC'?'直达星盘可见':
      s.direct_disc_visibility==='ATMOSPHERIC_EXTINCTION_BELOW_DISPLAY_FLOOR'?
        '几何可见但被大气消光':'几何地平线下方';
    document.querySelector('#planet-sky').textContent=scene.sky.map(s=>s.id+' 方位 '+
      s.azimuth_deg.toFixed(2)+'° / 高度 '+s.altitude_deg.toFixed(2)+'° · 大气外 '+
      s.top_of_atmosphere_irradiance_w_m2.toPrecision(4)+' W/m² / 地表直达代理 '+
      s.surface_direct_irradiance_proxy_w_m2.toPrecision(4)+' W/m² '+
      visibilityLabel(s)).join(' · ')+
      (ground?'':' · WebGL2不可用，保留地图与数值');
    document.querySelector('#planet-limitations').textContent='参数场景，不是地质/气候预测。'+
      scene.limitations.join(' ');
    window.currentPlanetExplorationScene=scene;
    ground?.load(scene);ground?.lockStar(lockSelect.value,scene);globe?.load(scene);
    document.querySelector('#planet-export').disabled=false;
    document.querySelector('#planet-goto-mountain').disabled=false;
    document.querySelector('#planet-goto-plain').disabled=false;
    status.textContent='场景 '+scene.scenario_sha256.slice(0,12)+' · '+scene.run_id+
      ' · 真实源采样 t='+scene.source.t_s+' SI s · '+scene.contour_levels_m.length+
      '级等高线 · 固定灰色平衡，不替代M3历史';
  }
  function readParameters(t_s){
    const parameters={t_s,composition_mode:
      document.querySelector('#planet-composition-mode').value};
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
    return parameters;
  }
  async function calculate(t_s,{quiet=false}={}){
    const serial=++requestSerial;
    try{
      if(!orbitSamples.length||!orbitLoadedRunId||
        document.querySelector('#observe-run-id').value!==orbitLoadedRunId){
        throw Error('请先读取真实轨道采样');
      }
      if(!quiet){clear();status.textContent='验证归档并计算参数化场景…';}
      const result=await window.ThreeBodyWeb.post('/api/v1/planet-scenarios',{
        run_id:orbitLoadedRunId,parameters:readParameters(t_s)});
      if(serial!==requestSerial)return;show(result,{redrawMap:!quiet});
    }catch(error){if(serial===requestSerial)status.textContent='无法计算：'+error.message;}
  }
  function build(){return calculate(orbitSamples[orbitIndex]?.t_s);}
  document.querySelector('#planet-build').addEventListener('click',build);

  function applyGeneratedPrior(){
    const body=(window.ThreeBodyWeb.getState('bodyMetadata')||linkedBodyMetadata)?.bodies?.['planet-1'];
    const prior=body?.atmosphere_hypothesis;
    if(!body||!prior){
      document.querySelector('#planet-prior-source').textContent='当前归档没有0.4.3大气先验；保留表单假设。';
      return;
    }
    const composition=prior.composition_mole_fraction;
    const values={atmosphere_mass_kg:prior.atmosphere_mass_kg,
      molar_mass_kg_mol:prior.mean_molar_mass_kg_mol||.029,
      oxygen_mole_fraction:composition.oxygen,water_vapor_mole_fraction:composition.water_vapor,
      carbon_dioxide_mole_fraction:composition.carbon_dioxide,argon_mole_fraction:composition.argon,
      nitrogen_dioxide_mole_fraction:composition.nitrogen_dioxide||0,
      methane_mole_fraction:composition.methane||0,
      sulfur_dioxide_mole_fraction:composition.sulfur_dioxide||0,
      hydrogen_mole_fraction:composition.hydrogen||0,helium_mole_fraction:composition.helium||0,
      ammonia_mole_fraction:composition.ammonia||0,
      albedo:prior.sampled_albedo,rotation_rate_rad_s:body.rotation_rate_rad_s};
    for(const [name,value] of Object.entries(values)){
      const input=document.querySelector('[data-planet="'+name+'"]');if(input)input.value=String(value);
    }
    document.querySelector('#planet-axis').value=JSON.stringify(body.rotation_axis_inertial_unit);
    document.querySelector('#planet-composition-mode').value='explicit';
    document.querySelector('#planet-prior-source').textContent=prior.status+' · Jeans代理 '+
      prior.jeans_parameter_proxy.toFixed(2)+' · 保留权重 '+prior.retention_weight.toFixed(3)+
      '；这是可复现先验，不是大气观测。';
    ++requestSerial;clear();status.textContent='已载入生成器先验；点击计算生成场景。';
  }
  document.querySelector('#planet-use-generated').addEventListener('click',applyGeneratedPrior);
  window.ThreeBodyWeb.subscribe('bodyMetadata',metadata=>{
    if(metadata?.bodies?.['planet-1']?.atmosphere_hypothesis)applyGeneratedPrior();
  });

  map.addEventListener('click',e=>{
    if(!scene)return;
    const rect=map.getBoundingClientRect();
    const c=Math.max(0,Math.min(47,Math.floor((e.clientX-rect.left)/rect.width*48)));
    const r=23-Math.max(0,Math.min(23,Math.floor((e.clientY-rect.top)/rect.height*24)));
    const region=scene.regions[r*48+c],wind=region.wind_speed_range_mps;
    document.querySelector('#planet-region').textContent=region.id+' · '+region.latitude_deg.toFixed(2)+'° / '+
      region.longitude_deg.toFixed(2)+'° · 海拔 '+region.elevation_m.toFixed(1)+' m · 水深 '+
      region.water_depth_m.toFixed(1)+' m · 水相代理 '+region.water_phase_proxy+' · '+
      region.temperature_k.toFixed(2)+' K · 风速先验范围 '+
      (wind?wind.map(v=>v.toFixed(2)).join('–')+' m/s':'不适用（真空）')+
      ' · 区域灾害条件概率 '+(region.conditional_hazard_probability==null?'未知（未提供率）':
        (100*region.conditional_hazard_probability).toFixed(3)+'%');
    document.querySelector('[data-planet="latitude_deg"]').value=region.latitude_deg;
    document.querySelector('[data-planet="longitude_deg"]').value=region.longitude_deg;
    document.querySelector('#planet-export').disabled=true;ground?.load(null);
    document.querySelector('#planet-sky').textContent='地点已改变，请重新计算此地点的天空。';
    status.textContent='已选 '+region.id+'；点击计算以更新此地点的恒星天空。';
  });
  function terrainSample(kind){
    if(!scene?.terrain_model)return;
    let target;
    if(kind==='plain'){
      const plain=scene.terrain_model.plains.reduce((best,item)=>
        !best||item.strength>best.strength?item:best,null);
      target=plain.center_unit;
    }else{
      let best={height:-Infinity,normal:null};
      for(const ridge of scene.terrain_model.ridges){
        const center=ridge.center_unit,normal=ridge.great_circle_normal_unit;
        const tangent=[normal[1]*center[2]-normal[2]*center[1],
          normal[2]*center[0]-normal[0]*center[2],
          normal[0]*center[1]-normal[1]*center[0]];
        for(let index=-20;index<=20;index++){
          const angle=ridge.extent_rad*index/20;
          const candidate=center.map((value,axis)=>
            value*Math.cos(angle)+tangent[axis]*Math.sin(angle));
          const latitude=Math.asin(Math.max(-1,Math.min(1,candidate[2])));
          const longitude=Math.atan2(candidate[1],candidate[0]);
          const height=planetSurfaceHeight(scene,latitude,longitude);
          if(height>best.height)best={height,normal:candidate};
        }
      }
      target=best.normal;
    }
    const latitude=Math.asin(Math.max(-1,Math.min(1,target[2])))*180/Math.PI;
    const longitude=Math.atan2(target[1],target[0])*180/Math.PI;
    document.querySelector('[data-planet="latitude_deg"]').value=latitude;
    document.querySelector('[data-planet="longitude_deg"]').value=longitude;
    status.textContent='正在按显式观测点重新计算'+(kind==='plain'?'平原':'山地')+'样本…';
    calculate(orbitSamples[orbitIndex].t_s);
  }
  document.querySelector('#planet-goto-mountain').addEventListener(
    'click',()=>terrainSample('mountain'));
  document.querySelector('#planet-goto-plain').addEventListener(
    'click',()=>terrainSample('plain'));
  document.querySelector('#planet-export').addEventListener('click',()=>{
    if(!scene)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(scene,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='planet-exploration.json';link.click();
    URL.revokeObjectURL(url);
  });
  const invalidate=()=>{++requestSerial;clear();status.textContent='采样或参数已更改，请重新计算；旧场景已清空。';};
  window.addEventListener('threebody-time',()=>{
    if(scene&&document.querySelector('#planet-follow-time').checked){
      clearTimeout(rebuildTimer);rebuildTimer=setTimeout(()=>calculate(orbitSamples[orbitIndex].t_s,{quiet:true}),120);
    }else invalidate();
  });
  for(const selector of ['#planet-parameters','#planet-composition-mode','#planet-hazard-rates','#planet-hazard-definition','#planet-axis']){
    document.querySelector(selector).addEventListener('input',invalidate);
  }
  document.querySelector('#observe-run-id').addEventListener('input',invalidate);
  lockSelect.addEventListener('change',()=>ground?.lockStar(lockSelect.value,scene));
  function stopPlayback(){if(playback!==null)clearInterval(playback);playback=null;}
  function step(delta){
    if(!orbitSamples.length)return;orbitSetIndex(Math.max(0,Math.min(orbitSamples.length-1,orbitIndex+delta)));
  }
  document.querySelector('#planet-ground-previous').addEventListener('click',()=>{stopPlayback();step(-1);});
  document.querySelector('#planet-ground-next').addEventListener('click',()=>{stopPlayback();step(1);});
  document.querySelector('#planet-ground-pause').addEventListener('click',stopPlayback);
  document.querySelector('#planet-ground-play').addEventListener('click',()=>{
    stopPlayback();playback=setInterval(()=>{
      if(orbitIndex>=orbitSamples.length-1){stopPlayback();return;}step(1);
    },1000);
  });
  window.ThreeBodyWeb.onRoute('surface',{leave:stopPlayback,enter:()=>ground?.draw()});
})();
