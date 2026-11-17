/* M6.3-b: experimental WebGL2 projection. GPU receives ONLY verified M5A samples.
 * Unit conversion and origin subtraction are display-only. No N-body integration.
 */
(function(global){
  'use strict';
  const IDs=['star-1','star-2','star-3','planet-1'];
  const COLORS=[[.95,.64,.29],[.89,.45,.35],[.55,.72,1.0],[.84,.91,.86]];
  const VS=`#version 300 es
    in vec3 a_position;
    uniform float u_yaw;
    uniform float u_pitch;
    uniform float u_point_size;
    uniform vec3 u_focus;
    uniform float u_zoom;
    void main(){
      float cy=cos(u_yaw),sy=sin(u_yaw),cp=cos(u_pitch),sp=sin(u_pitch);
      vec3 v=(a_position-u_focus)/u_zoom;
      vec3 p=vec3(
        cy*v.x-sy*v.y,
        sy*v.x+cy*v.y,
        v.z
      );
      vec3 q=vec3(p.x,cp*p.z-sp*p.y,sp*p.z+cp*p.y);
      float w=3.6+q.z*0.25;
      gl_Position=vec4(q.x*2.1,q.y*2.1,q.z*0.15,w);
      gl_PointSize=u_point_size;
    }`;
  const FS=`#version 300 es
    precision highp float;
    uniform vec3 u_color;
    out vec4 fragColor;
    void main(){ fragColor=vec4(u_color,1.0); }`;
  function compile(gl,type,source){
    const shader=gl.createShader(type);
    gl.shaderSource(shader,source);gl.compileShader(shader);
    if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){
      const error=gl.getShaderInfoLog(shader);gl.deleteShader(shader);
      throw new Error('WebGL shader compilation failed: '+error);
    }
    return shader;
  }
  function create(canvas,onFailure,onSelect){
    const gl=canvas.getContext('webgl2',{
      antialias:true,alpha:false,preserveDrawingBuffer:true,
    });
    if(!gl) throw new Error('此浏览器不支持WebGL2');
    const vert=compile(gl,gl.VERTEX_SHADER,VS);
    const frag=compile(gl,gl.FRAGMENT_SHADER,FS);
    const program=gl.createProgram();
    gl.attachShader(program,vert);gl.attachShader(program,frag);
    gl.linkProgram(program);
    gl.deleteShader(vert);gl.deleteShader(frag);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS)){
      throw new Error('WebGL shader link failed: '+gl.getProgramInfoLog(program));
    }
    gl.useProgram(program);
    const pos=gl.getAttribLocation(program,'a_position');
    const yawLoc=gl.getUniformLocation(program,'u_yaw');
    const pitchLoc=gl.getUniformLocation(program,'u_pitch');
    const sizeLoc=gl.getUniformLocation(program,'u_point_size');
    const focusLoc=gl.getUniformLocation(program,'u_focus');
    const zoomLoc=gl.getUniformLocation(program,'u_zoom');
    const colorLoc=gl.getUniformLocation(program,'u_color');
    const buffer=gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos,3,gl.FLOAT,false,0,0);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    let yaw=.5,pitch=.25,zoom=1,drag=null,disposed=false;
    let focusId=null,selectedBody=null;
    let normalized=null,sourceCount=0;
    function fail(){disposed=true;onFailure('WebGL上下文丢失，已恢复Canvas观察器');}
    canvas.addEventListener('webglcontextlost',(event)=>{
      event.preventDefault();fail();
    });
    canvas.addEventListener('pointerdown',(event)=>{
      drag=[event.clientX,event.clientY,event.clientX,event.clientY];
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',(event)=>{
      if(!drag)return;
      yaw+=(event.clientX-drag[0])*.006;
      pitch=Math.max(-1.45,Math.min(1.45,
        pitch+(event.clientY-drag[1])*.006));
      drag=[event.clientX,event.clientY,drag[2],drag[3]];
      if(normalized)draw(normalized.selectedIndex);
    });
    canvas.addEventListener('pointerup',(event)=>{
      if(drag && Math.hypot(event.clientX-drag[2],event.clientY-drag[3])<6) {
        const picked=pickAt(event.clientX,event.clientY);
        if(picked && typeof onSelect==='function')onSelect(picked);
      }
      drag=null;
    });
    canvas.addEventListener('pointercancel',()=>{drag=null;});
    canvas.addEventListener('wheel',(event)=>{
      event.preventDefault();zoom=Math.max(.2,Math.min(15,zoom*
        (event.deltaY>0?1.12:.89)));
      if(normalized)draw(normalized.selectedIndex);
    },{passive:false});
    function load(samples){
      if(disposed)return;
      if(!samples.length){normalized=null;sourceCount=0;return;}
      // Rebase large absolute SI positions in Float64 *before* GPU conversion.
      // Fix origin and scene scale across every frame of the selected window.
      const first=samples[0].positions_m;
      const center=[0,1,2].map(k=>IDs.reduce(
        (sum,id)=>sum+first[id][k],0)/IDs.length);
      let extent=1;
      for(const s of samples)for(const id of IDs){
        const p=s.positions_m[id];
        extent=Math.max(extent,Math.hypot(
          p[0]-center[0],p[1]-center[1],p[2]-center[2]
        ));
      }
      const coords=IDs.map(id=>new Float32Array(samples.flatMap(s=>
        s.positions_m[id].map((x,k)=>(x-center[k])/extent)
      )));
      normalized={coords,samples,selectedIndex:0};
      sourceCount=samples.length;
    }
    function focusVector(i){
      if(!focusId || !normalized)return [0,0,0];
      const coords=normalized.coords[IDs.indexOf(focusId)];
      return [coords[i*3],coords[i*3+1],coords[i*3+2]];
    }
    function projectedPoint(body,index) {
      if(!normalized)return null;
      const coords=normalized.coords[body];
      const center=focusVector(index);
      const x=(coords[index*3]-center[0])/zoom;
      const y=(coords[index*3+1]-center[1])/zoom;
      const z=(coords[index*3+2]-center[2])/zoom;
      const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
      const pX=cy*x-sy*y, pY=sy*x+cy*y;
      const qY=cp*z-sp*pY,qZ=sp*z+cp*pY;
      const w=3.6+qZ*.25;
      if(w<=0)return null;
      return [canvas.width*(1+2.1*pX/w)/2,
        canvas.height*(1-2.1*qY/w)/2];
    }
    function pickAt(clientX,clientY){
      if(disposed || !normalized)return null;
      const bounds=canvas.getBoundingClientRect();
      const x=(clientX-bounds.left)*canvas.width/bounds.width;
      const y=(clientY-bounds.top)*canvas.height/bounds.height;
      let closest=null,dist=Infinity;
      for(let i=0;i<IDs.length;i++){
        const p=projectedPoint(i,normalized.selectedIndex);
        if(!p)continue;
        const d=Math.hypot(p[0]-x,p[1]-y);
        if(d<dist){closest=IDs[i];dist=d;}
      }
      // Hit radius is in backing pixels and compensates for high-DPI CSS scale.
      return dist<=20?closest:null;
    }
    function draw(index){
      if(disposed)return;
      gl.viewport(0,0,canvas.width,canvas.height);
      gl.clearColor(.031,.047,.051,1);
      gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
      if(!normalized)return;
      const i=Math.min(sourceCount-1,Math.max(0,Math.trunc(index)));
      normalized.selectedIndex=i;
      gl.useProgram(program);
      gl.uniform1f(yawLoc,yaw);
      gl.uniform1f(pitchLoc,pitch);
      gl.uniform1f(zoomLoc,zoom);
      gl.uniform3fv(focusLoc,focusVector(i));
      // Zoom adjusts visible scene scale, never source coordinates.
      for(let body=0;body<4;body++){
        const data=normalized.coords[body];
        gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
        gl.bufferData(gl.ARRAY_BUFFER,data.subarray(0,(i+1)*3),gl.DYNAMIC_DRAW);
        gl.uniform3fv(colorLoc,COLORS[body]);
        gl.uniform1f(sizeLoc,1);
        gl.drawArrays(gl.LINE_STRIP,0,i+1);
        gl.uniform1f(sizeLoc,body===3?7:(
          selectedBody===IDs[body]?17:12));
        gl.drawArrays(gl.POINTS,i,1);
      }
    }
    function focus(id){
      if(id!==null && !IDs.includes(id))throw new Error('invalid focus body');
      focusId=id;
      if(normalized)draw(normalized.selectedIndex);
    }
    function select(id){
      if(id!==null && !IDs.includes(id))throw new Error('invalid selected body');
      selectedBody=id;
      if(normalized)draw(normalized.selectedIndex);
    }
    function rotate(axis,delta){
      if(axis==='yaw')yaw+=delta;
      else if(axis==='pitch')pitch=Math.max(-1.45,Math.min(1.45,pitch+delta));
      if(normalized)draw(normalized.selectedIndex);
    }
    function resetView(){
      yaw=.5;pitch=.25;zoom=1;focusId=null;
      if(normalized)draw(normalized.selectedIndex);
    }
    return Object.freeze({
      load,draw,focus,select,rotate,resetView,pickAt,
      supported:true,
      dispose(){disposed=true;gl.deleteBuffer(buffer);gl.deleteProgram(program);},
    });
  }
  global.createM63WebGL=create;
})(window);
