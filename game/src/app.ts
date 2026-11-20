import {FreeCamera} from '@babylonjs/core/Cameras/freeCamera';
import {Engine} from '@babylonjs/core/Engines/engine';
import {DirectionalLight} from '@babylonjs/core/Lights/directionalLight';
import {HemisphericLight} from '@babylonjs/core/Lights/hemisphericLight';
import {StandardMaterial} from '@babylonjs/core/Materials/standardMaterial';
import {Color3,Color4,Vector3} from '@babylonjs/core/Maths/math';
import {Mesh} from '@babylonjs/core/Meshes/mesh';
import {MeshBuilder} from '@babylonjs/core/Meshes/meshBuilder';
import {Scene} from '@babylonjs/core/scene';
import {bodies,environmentPoint,listRuns,scenario,trajectory,type Body,type EnvironmentPoint,type Scenario,type TrajectoryPoint} from './api';
import {environmentField,formatField,surfaceStyle,weatherState} from './environment';
import {dot,length,normalize,planetSurfaceBasis,stellarAngularRadius,sub,type Vec3} from './physics';
import {loadPlayer,storePlayer} from './save';
import {TerrainManager} from './terrain-manager';
import {WeatherVisual} from './weather-visual';

interface Ui{canvas:HTMLCanvasElement;chooser:HTMLElement;runs:HTMLSelectElement;state:HTMLElement;hud:HTMLElement;map:HTMLElement;crosshair:HTMLElement;time:HTMLInputElement;timeText:HTMLElement;play:HTMLButtonElement;speed:HTMLSelectElement;quality:HTMLSelectElement}
interface Metrics{firstFrameMs:number|null;frameP50Ms:number|null;frameP95Ms:number|null;rebaseP95Ms:number|null;readyMs:number|null;samples:number;heapUsedMiB:number|null;transferMiB:number}
declare global{interface Window{__P1_GAME_READY__?:boolean;__P1_METRICS__?:Metrics;__P1_DIAGNOSTICS__?:{runId:string;latitudeDeg:number;longitudeDeg:number;terrainMeshes:number;uniqueTileKeys:number;environmentHash:string|null;webGlVersion:number;firstPerson:boolean}}}
function get<T extends HTMLElement>(selector:string):T{const node=document.querySelector<T>(selector);if(!node)throw Error('missing '+selector);return node}
function pct(values:number[],p:number){if(!values.length)return null;const a=[...values].sort((x,y)=>x-y);return a[Math.floor((a.length-1)*p)]??null}
function makeUi(root:HTMLElement):Ui{
 root.innerHTML=[
  '<canvas id="world"></canvas><section id="chooser"><h1>三体星 · 行星探索</h1>',
  '<p>选择具有 M5A 与 M7.2/M7.3 归档的世界</p><select id="runs"></select>',
  '<button id="enter">进入世界</button><p id="state"></p></section>',
  '<div id="hud" hidden><b id="name"></b> <span id="quality-state"></span>',
  '<div id="position"></div><div id="environment"></div><div id="stars"></div>',
  '<div id="weather"></div><div id="material"></div><div class="timeline">',
  '<button id="play">暂停</button><input id="time" type="range"><span id="time-text"></span>',
  '<select id="speed"><option value="1">1×</option><option value="60" selected>60×</option>',
  '<option value="3600">3600×</option></select></div>',
  '<small>WASD · Shift 奔跑 · Space 跳跃 · V 视角 · R 重置 · Esc 释放鼠标</small></div>',
  '<aside id="map" hidden><canvas width="300" height="160"></canvas><div>全球位置 · 黄色为玩家</div></aside>',
  '<aside id="settings" hidden>画质 <select id="graphics-quality"><option value="1">高</option>',
  '<option value="1.5" selected>中</option><option value="2">低</option></select></aside>',
  '<div id="crosshair" hidden>＋</div>',
 ].join('');
 return{canvas:get('#world'),chooser:get('#chooser'),runs:get('#runs'),state:get('#state'),hud:get('#hud'),map:get('#map'),crosshair:get('#crosshair'),time:get('#time'),timeText:get('#time-text'),play:get('#play'),speed:get('#speed'),quality:get('#graphics-quality')};
}

class Game{
 private engine:Engine;private scene:Scene;private camera:FreeCamera;private player:Mesh;
 private terrain!:TerrainManager;private weather!:WeatherVisual;private sc!:Scenario;
 private bs:Body[]=[];private path:TrajectoryPoint[]=[];private hash='';
 private t=0;private startT=0;private endT=0;private lat=0;private lon=0;private height=2;private vy=0;
 private first=false;private playing=true;private keys=new Set<string>();private lights:DirectionalLight[]=[];
 private disks:Mesh[]=[];private env:EnvironmentPoint|null=null;private lastEnv=-Infinity;private serial=0;
 private lastFrame=performance.now();private frames:number[]=[];private rebases:number[]=[];private ready:number|null=null;private mapBase:HTMLCanvasElement|null=null;
 constructor(private id:string,private ui:Ui){
  this.engine=new Engine(ui.canvas,true,{preserveDrawingBuffer:false,stencil:false});
  this.scene=new Scene(this.engine);this.scene.clearColor=new Color4(.02,.04,.07,1);
  this.camera=new FreeCamera('camera',new Vector3(0,4,-8),this.scene);this.camera.minZ=.05;this.camera.maxZ=500000;this.camera.fov=1.05;
  this.player=MeshBuilder.CreateCapsule('player',{height:1.8,radius:.35},this.scene);
  const pm=new StandardMaterial('player-material',this.scene);pm.diffuseColor=new Color3(.8,.4,.15);this.player.material=pm;
  new HemisphericLight('sky-proxy',new Vector3(0,1,0),this.scene).intensity=.15;this.controls();
 }
 async init(){
  const[meta,track]=await Promise.all([bodies(this.id),trajectory(this.id)]);
  if(!track.samples.length)throw Error('M5A 没有已提交轨道');
  this.bs=meta.bodies;this.hash=meta.initial_state_sha256;this.path=track.samples;this.startT=track.range_start_s;this.endT=track.range_end_s;this.t=this.startT;
  this.sc=await scenario(this.id,this.t);this.startT=Math.max(this.startT,this.sc.integrated_environment_coverage_s[0]);this.endT=Math.min(this.endT,this.sc.integrated_environment_coverage_s[1]);this.t=this.startT;if(this.startT>this.endT)throw Error('M5A 与环境归档没有共同时间覆盖');const saved=loadPlayer(this.id,this.hash);
  if(saved){this.lat=saved.latitudeDeg;this.lon=saved.longitudeDeg;this.height=saved.heightM;this.first=saved.camera==='first'}
  this.terrain=new TerrainManager(this.scene,this.sc,this.lat,this.lon);this.weather=new WeatherVisual(this.scene);this.makeStars();this.timeline();
  await this.refresh(true);this.map();this.engine.runRenderLoop(()=>this.frame());setInterval(()=>this.save(),5000);addEventListener('beforeunload',()=>this.save());
  this.ready=performance.now();window.__P1_GAME_READY__=true;this.metrics();
 }
 private controls(){
  addEventListener('keydown',e=>{this.keys.add(e.code);if(e.code==='KeyV')this.first=!this.first;if(e.code==='KeyR')this.reset()});
  addEventListener('keyup',e=>this.keys.delete(e.code));this.ui.canvas.onclick=()=>void this.ui.canvas.requestPointerLock();
  addEventListener('mousemove',e=>{if(document.pointerLockElement!==this.ui.canvas)return;this.camera.rotation.y+=e.movementX*.002;this.camera.rotation.x=Math.max(-1.35,Math.min(1.35,this.camera.rotation.x+e.movementY*.002))});
  addEventListener('resize',()=>this.engine.resize());this.ui.play.onclick=()=>{this.playing=!this.playing;this.ui.play.textContent=this.playing?'暂停':'播放'};
  this.ui.time.oninput=()=>{this.t=Number(this.ui.time.value);this.playing=false;this.ui.play.textContent='播放';void this.refresh(true)};
  this.ui.quality.onchange=()=>this.engine.setHardwareScalingLevel(Number(this.ui.quality.value));
 }
 private timeline(){this.ui.time.min=String(this.startT);this.ui.time.max=String(this.endT);this.ui.time.step=String(Math.max(.001,(this.endT-this.startT)/20000));this.ui.time.value=String(this.t)}
 private makeStars(){const colors=[new Color3(1,.5,.2),new Color3(1,.9,.6),new Color3(.6,.75,1)];for(let i=0;i<3;i++){const light=new DirectionalLight('star-light-'+i,new Vector3(0,-1,0),this.scene);light.diffuse=colors[i]!;this.lights.push(light);const disk=MeshBuilder.CreateSphere('star-disk-'+i,{diameter:1,segments:24},this.scene),mat=new StandardMaterial('star-'+i,this.scene);mat.emissiveColor=colors[i]!;mat.disableLighting=true;disk.material=mat;this.disks.push(disk)}}
 private sample(t:number):TrajectoryPoint{
  let hi=this.path.findIndex(s=>s.t_s>=t);if(hi<=0)return this.path[0]!;if(hi<0)return this.path.at(-1)!;
  const a=this.path[hi-1]!,b=this.path[hi]!,q=(t-a.t_s)/(b.t_s-a.t_s),positions:Record<string,Vec3>={};
  for(const id of['star-1','star-2','star-3','planet-1']){const x=a.positions_m[id]!,y=b.positions_m[id]!;positions[id]=[x[0]+q*(y[0]-x[0]),x[1]+q*(y[1]-x[1]),x[2]+q*(y[2]-x[2])]}
  return{t_s:t,positions_m:positions};
 }
 private transmission(alt:number){
  if(alt<=-.02)return 0;const pressure=this.env?environmentField(this.env,'surface_pressure').value:null;if(pressure===null||pressure<=0)return alt>0?1:0;
  const deg=Math.max(-1,alt*180/Math.PI),air=1/(Math.sin(Math.max(0,alt))+.50572*(deg+6.07995)**-1.6364);
  const gas=(this.env?.gas_mole_fractions as Record<string,number>|undefined)??{};
  const absorber=2.5*(gas.nitrogen_dioxide??0)+.8*(gas.sulfur_dioxide??0)+.35*(gas.methane??0)+.2*(gas.water_vapor??0);
  return Math.exp(-Math.min(40,pressure/101325*(.1+absorber)*Math.min(40,air)));
 }
 private stars(){
  const s=this.sample(this.t),planet=s.positions_m['planet-1']!,body=this.bs.find(x=>x.id==='planet-1');if(!body)return;
  const basis=planetSurfaceBasis(body.rotation_axis_inertial_unit??[0,0,1],this.lat,this.lon,(body.rotation_rate_rad_s??0)*this.t),text:string[]=[];
  for(let i=0;i<3;i++){const star=this.bs[i]!,delta=sub(s.positions_m[star.id]!,planet),distance=length(delta),direction=normalize(delta),local:Vec3=[dot(direction,basis.east),dot(direction,basis.up),dot(direction,basis.north)],alt=Math.asin(Math.max(-1,Math.min(1,local[1]))),angle=stellarAngularRadius(star.radius_m,distance),irr=(star.luminosity_w??0)/(4*Math.PI*distance**2),trans=this.transmission(alt),light=this.lights[i]!,disk=this.disks[i]!,range=1000;
   light.direction=new Vector3(-local[0],-local[1],-local[2]);light.intensity=Math.min(4,Math.max(0,local[1])*irr*trans/800);
   disk.position.set(local[0]*range,local[1]*range,local[2]*range);disk.scaling.setAll(2*Math.tan(angle)*range);disk.visibility=trans;disk.isVisible=alt>-angle;
   text.push(star.id+' 高度 '+(alt*180/Math.PI).toFixed(1)+'° 直径 '+(2*angle*180/Math.PI).toFixed(3)+'° 透过率 '+trans.toFixed(3));
  }get('#stars').textContent=text.join('　');
 }
 private async refresh(force=false){
  const now=performance.now();if(!force&&now-this.lastEnv<2000)return;this.lastEnv=now;const serial=++this.serial;
  try{const e=await environmentPoint(this.id,this.t,this.lat,this.lon);if(serial!==this.serial)return;this.env=e;const style=surfaceStyle(e);this.terrain.setSurfaceStyle(style);this.weather.update(e,this.player.position);
   const temp=environmentField(e,'surface_temperature'),air=environmentField(e,'air_temperature'),pressure=environmentField(e,'surface_pressure'),humidity=environmentField(e,'relative_humidity'),east=environmentField(e,'eastward_wind'),north=environmentField(e,'northward_wind'),cloud=environmentField(e,'cloud_fraction'),rain=environmentField(e,'precipitation_rate'),w=weatherState(e);
   get('#environment').textContent='地表 '+formatField(temp)+'　空气 '+formatField(air)+'　气压 '+formatField(pressure,0)+'　湿度 '+(humidity.value===null?'不可用 ['+humidity.quality+']':(humidity.value*100).toFixed(1)+'% ['+humidity.quality+']');
   get('#weather').textContent='天气 '+w.state+'　相态 '+w.phase+'　风 '+(east.value===null||north.value===null?'不可用':Math.hypot(east.value,north.value).toFixed(1)+' m/s')+'　云 '+(cloud.value===null?'不可用':(cloud.value*100).toFixed(0)+'%')+'　降水 '+formatField(rain,6)+(w.events.length?'　事件 '+w.events.join(', '):'');
   get('#material').textContent='地表相态：液水 '+(style.liquidWater*100).toFixed(1)+'%　冰 '+(style.ice*100).toFixed(1)+'%　熔融物质 '+(style.magma*100).toFixed(1)+'%　'+(style.uncertain?'UNCERTAIN':'归档计算');
   get('#quality-state').textContent='模型 '+String(e.model_id??'unknown')+' · '+String(e.fidelity_mode??'quality unknown')+' · '+String(e.result_sha256??'').slice(0,12);
   const c=cloud.value??0;this.scene.fogMode=c>.35?Scene.FOGMODE_EXP2:Scene.FOGMODE_NONE;this.scene.fogDensity=.000012*c;this.scene.clearColor=temp.value!==null&&temp.value>700?new Color4(.18,.05,.02,1):new Color4(.02+.12*c,.04+.14*c,.07+.18*c,1);
  }catch(error){if(serial!==this.serial)return;this.env=null;get('#environment').textContent='环境不可用（不使用虚构值）：'+String(error);get('#weather').textContent='天气 UNAVAILABLE';get('#material').textContent='地表相态 UNAVAILABLE';get('#quality-state').textContent='UNAVAILABLE'}
 }
 private frame(){
  const now=performance.now(),dt=Math.min(.05,(now-this.lastFrame)/1000);this.frames.push(now-this.lastFrame);if(this.frames.length>600)this.frames.shift();this.lastFrame=now;
  if(this.playing){this.t=Math.min(this.endT,this.t+dt*Number(this.ui.speed.value));this.ui.time.value=String(this.t);if(this.t>=this.endT){this.playing=false;this.ui.play.textContent='播放'}}
  let forward=0,side=0;if(this.keys.has('KeyW'))forward++;if(this.keys.has('KeyS'))forward--;if(this.keys.has('KeyD'))side++;if(this.keys.has('KeyA'))side--;const mag=Math.hypot(forward,side);if(mag>1){forward/=mag;side/=mag}
  const speed=this.keys.has('ShiftLeft')?12:5,yaw=this.camera.rotation.y,east=(Math.sin(yaw)*forward+Math.cos(yaw)*side)*speed*dt,north=(Math.cos(yaw)*forward-Math.sin(yaw)*side)*speed*dt,r=this.sc.radius_m;
  this.lat=Math.max(-89.9,Math.min(89.9,this.lat+north/r*180/Math.PI));this.lon=((this.lon+east/(r*Math.max(1e-5,Math.cos(this.lat*Math.PI/180)))*180/Math.PI+540)%360)-180;
  if(this.keys.has('Space')&&this.height<=2.001)this.vy=5;this.vy-=this.sc.gravity_m_s2*dt;this.height+=this.vy*dt;if(this.height<2){this.height=2;this.vy=0}
  const started=performance.now(),location=this.terrain.locate(this.lat,this.lon);if(location.rebased)this.rebases.push(performance.now()-started);
  const x=location.eastM,y=location.groundY+this.height,z=location.northM;this.player.position.set(x,y+.9,z);this.player.isVisible=!this.first;
  this.camera.position=this.first?new Vector3(x,y+1.65,z):new Vector3(x-Math.sin(yaw)*7,y+4,z-Math.cos(yaw)*7);this.weather.tick(dt,this.player.position);
  this.stars();void this.refresh();get('#position').textContent='纬度 '+this.lat.toFixed(6)+'°　经度 '+this.lon.toFixed(6)+'°　海拔 '+location.elevationM.toFixed(1)+' m　离地 '+this.height.toFixed(2)+' m';get('#name').textContent=this.id;this.ui.timeText.textContent='t='+this.t.toFixed(2)+' / '+this.endT.toFixed(2)+' s';this.map();this.scene.render();this.diagnostics();if(this.frames.length%120===0)this.metrics();
 }
 private map(){const canvas=this.ui.map.querySelector('canvas');if(!(canvas instanceof HTMLCanvasElement))return;const c=canvas.getContext('2d');if(!c)return;if(!this.mapBase){const base=document.createElement('canvas');base.width=150;base.height=80;const b=base.getContext('2d')!,image=b.createImageData(base.width,base.height);for(let y=0;y<base.height;y++)for(let x=0;x<base.width;x++){const lat=90-(y+.5)/base.height*180,lon=-180+(x+.5)/base.width*360,h=this.terrain.elevation(lat,lon),offset=(y*base.width+x)*4,wet=h<this.sc.sea_level_m,snow=!wet&&h>this.sc.parameters.relief_m*.45;image.data[offset]=wet?10:snow?220:h>0?105:45;image.data[offset+1]=wet?55:snow?225:h>0?82:80;image.data[offset+2]=wet?90:snow?235:h>0?48:42;image.data[offset+3]=255}b.putImageData(image,0,0);this.mapBase=base}c.imageSmoothingEnabled=true;c.drawImage(this.mapBase,0,0,canvas.width,canvas.height);c.strokeStyle='#ffffff44';for(let row=0;row<=8;row++){c.beginPath();c.moveTo(0,row*canvas.height/8);c.lineTo(canvas.width,row*canvas.height/8);c.stroke()}for(let col=0;col<=12;col++){c.beginPath();c.moveTo(col*canvas.width/12,0);c.lineTo(col*canvas.width/12,canvas.height);c.stroke()}c.fillStyle='#ffd35a';c.beginPath();c.arc((this.lon+180)/360*canvas.width,(90-this.lat)/180*canvas.height,4,0,Math.PI*2);c.fill();c.strokeStyle='#ffd35a';c.strokeRect(canvas.width-72,canvas.height-52,64,44);c.fillStyle='#08131dcc';c.fillRect(canvas.width-71,canvas.height-51,62,42);c.fillStyle='#ffd35a';c.fillText('局部 LOD 12',canvas.width-67,canvas.height-28);c.beginPath();c.arc(canvas.width-40,canvas.height-17,3,0,Math.PI*2);c.fill()}
 private reset(){const started=performance.now();this.lat=0;this.lon=0;this.height=2;this.vy=0;this.terrain.dispose();this.terrain=new TerrainManager(this.scene,this.sc,0,0);this.rebases.push(performance.now()-started);if(this.env)this.terrain.setSurfaceStyle(surfaceStyle(this.env));void this.refresh(true);this.metrics()}
 private save(){storePlayer({schema:'p1-player-save-v1',runId:this.id,sourceHash:this.hash,latitudeDeg:this.lat,longitudeDeg:this.lon,heightM:this.height,camera:this.first?'first':'third',savedAt:new Date().toISOString()})}
 private diagnostics(){window.__P1_DIAGNOSTICS__={runId:this.id,latitudeDeg:this.lat,longitudeDeg:this.lon,terrainMeshes:this.terrain.meshCount,uniqueTileKeys:new Set(this.terrain.tileKeys).size,environmentHash:this.env?String(this.env.result_sha256??''):null,webGlVersion:this.engine.webGLVersion,firstPerson:this.first}}
 private metrics(){const perf=performance as Performance&{memory?:{usedJSHeapSize:number}};const transfer=performance.getEntriesByType('resource').reduce((sum,entry)=>sum+((entry as PerformanceResourceTiming).transferSize||0),0);window.__P1_METRICS__={firstFrameMs:this.frames[0]??null,frameP50Ms:pct(this.frames.slice(30),.5),frameP95Ms:pct(this.frames.slice(30),.95),rebaseP95Ms:pct(this.rebases,.95),readyMs:this.ready,samples:this.frames.length,heapUsedMiB:perf.memory?perf.memory.usedJSHeapSize/1048576:null,transferMiB:transfer/1048576};this.diagnostics()}
}

export async function start(root:HTMLElement){
 const ui=makeUi(root);
 try{const runs=await listRuns();ui.runs.innerHTML=runs.map(run=>'<option value=\"'+run.run_id+'\">'+run.run_id+' · '+run.status+'</option>').join('');if(!runs.length){ui.state.textContent='没有可玩的 M5A 世界';return}
  get<HTMLButtonElement>('#enter').onclick=async()=>{ui.state.textContent='校验归档并构建球面分块地形…';try{const game=new Game(ui.runs.value,ui);await game.init();ui.chooser.hidden=true;ui.hud.hidden=false;ui.map.hidden=false;ui.crosshair.hidden=false;get('#settings').hidden=false}catch(error){ui.state.textContent='进入失败：'+String(error)}};
 }catch(error){ui.state.textContent='世界列表失败：'+String(error)}
}
