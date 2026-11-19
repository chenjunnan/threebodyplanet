/* Read-only whole-planet WebGL view using the exported terrain/optics contract. */
(function(global){
  'use strict';
  function create(canvas){
    const gl=canvas.getContext('webgl2',{antialias:true,preserveDrawingBuffer:true});
    if(!gl)return null;
    const compile=(type,source)=>{
      const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
      if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const vertex=compile(gl.VERTEX_SHADER,`#version 300 es
      in vec3 position;in vec3 normal;in vec3 color;out vec3 surfaceColor;out vec3 rotatedNormal;
      uniform float yaw;uniform float pitch;
      vec3 rotateView(vec3 p){
        p=vec3(cos(yaw)*p.x+sin(yaw)*p.z,p.y,-sin(yaw)*p.x+cos(yaw)*p.z);
        return vec3(p.x,cos(pitch)*p.y+sin(pitch)*p.z,-sin(pitch)*p.y+cos(pitch)*p.z);
      }
      void main(){
        vec3 p=rotateView(position);rotatedNormal=normalize(rotateView(normal));surfaceColor=color;
        gl_Position=vec4(p.x*.91,p.y*.91,-p.z*.45,1.0);
      }`);
    const fragment=compile(gl.FRAGMENT_SHADER,`#version 300 es
      precision highp float;in vec3 surfaceColor;in vec3 rotatedNormal;out vec4 result;
      uniform vec3 starDirection[3];uniform float starWeight[3];uniform vec3 atmosphereColor;
      uniform float meanOpticalDepth;uniform float diffuseLight;
      void main(){
        float direct=0.0;
        for(int i=0;i<3;i++)direct+=starWeight[i]*max(0.0,dot(rotatedNormal,starDirection[i]));
        float light=.035+diffuseLight+1.35*direct;
        vec3 rock=surfaceColor*sqrt(max(0.0,light));
        float viewCos=max(.025,rotatedNormal.z);
        float limbColumn=min(30.0,meanOpticalDepth/viewCos);
        float haze=(1.0-exp(-limbColumn))*.82;
        result=vec4(mix(rock,atmosphereColor,haze),1.0);
      }`);
    const program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);
    gl.linkProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
    const buffer=gl.createBuffer();let vertices=[],yaw=-.55,pitch=.28,drag=null;
    const loc=name=>gl.getUniformLocation(program,name);
    function draw(scene){
      gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.006,.009,.018,1);
      gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);if(!scene||!vertices.length)return;
      gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);gl.useProgram(program);
      gl.uniform1f(loc('yaw'),yaw);gl.uniform1f(loc('pitch'),pitch);
      const sky=scene.atmospheric_optics.sky_display_srgb||[0,0,0];
      gl.uniform3fv(loc('atmosphereColor'),sky.map(value=>.025+.85*value));
      const depths=scene.atmospheric_optics.optical_depth_rgb||[0,0,0];
      gl.uniform1f(loc('meanOpticalDepth'),depths.reduce((a,b)=>a+b,0)/3);
      const scattered=scene.atmospheric_optics.sky_scattered_intensity_proxy_w_m2||0;
      gl.uniform1f(loc('diffuseLight'),.28*(1-Math.exp(-scattered/1000)));
      const total=Math.max(1e-30,scene.sky.reduce((sum,star)=>
        sum+star.top_of_atmosphere_irradiance_w_m2,0));
      const observerLat=scene.parameters.latitude_deg*Math.PI/180;
      const observerLon=scene.parameters.longitude_deg*Math.PI/180;
      const up=[Math.cos(observerLat)*Math.cos(observerLon),
        Math.cos(observerLat)*Math.sin(observerLon),Math.sin(observerLat)];
      const east=[-Math.sin(observerLon),Math.cos(observerLon),0];
      const north=[-Math.sin(observerLat)*Math.cos(observerLon),
        -Math.sin(observerLat)*Math.sin(observerLon),Math.cos(observerLat)];
      for(let index=0;index<3;index++){
        const star=scene.sky[index],az=star.azimuth_deg*Math.PI/180;
        const alt=star.altitude_deg*Math.PI/180,ca=Math.cos(alt);
        const world=up.map((value,axis)=>value*Math.sin(alt)+
          east[axis]*Math.sin(az)*ca+north[axis]*Math.cos(az)*ca);
        const rotated=[Math.cos(yaw)*world[0]+Math.sin(yaw)*world[2],world[1],
          -Math.sin(yaw)*world[0]+Math.cos(yaw)*world[2]];
        const view=[rotated[0],Math.cos(pitch)*rotated[1]+Math.sin(pitch)*rotated[2],
          -Math.sin(pitch)*rotated[1]+Math.cos(pitch)*rotated[2]];
        gl.uniform3fv(loc('starDirection['+index+']'),view);
        gl.uniform1f(loc('starWeight['+index+']'),star.top_of_atmosphere_irradiance_w_m2/total);
      }
      gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,
        new Float32Array(vertices),gl.DYNAMIC_DRAW);
      for(const [name,offset] of [['position',0],['normal',12],['color',24]]){
        const attribute=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(attribute);
        gl.vertexAttribPointer(attribute,3,gl.FLOAT,false,36,offset);
      }
      gl.drawArrays(gl.TRIANGLES,0,vertices.length/9);
    }
    function load(scene){
      vertices=[];if(!scene){draw(null);return;}
      const point=(lat,lon)=>{
        const normal=[Math.cos(lat)*Math.cos(lon),Math.cos(lat)*Math.sin(lon),Math.sin(lat)];
        const elevation=global.planetSurfaceHeight(scene,lat,lon);
        const row=Math.max(0,Math.min(23,Math.floor((1+Math.sin(lat))*12)));
        const phase=scene.regions[row*48].potential_water_phase;
        const wet=scene.has_surface_water&&['ice','liquid_possible'].includes(phase)&&
          elevation<scene.sea_level_m;
        const surface=wet?scene.sea_level_m:elevation;
        const visualRadius=1+Math.max(-.07,Math.min(.07,surface/scene.radius_m*24));
        const h=Math.max(0,Math.min(1,(elevation/scene.parameters.relief_m+1)/2));
        const color=wet?(phase==='ice'?[.62,.76,.82]:[.025,.18,.29]):
          [.20+.34*h,.16+.27*h,.10+.16*h];
        return [...normal.map(value=>value*visualRadius),...normal,...color];
      };
      const latitudeSteps=64,longitudeSteps=128;
      for(let row=0;row<latitudeSteps;row++)for(let column=0;column<longitudeSteps;column++){
        const lat0=-Math.PI/2+Math.PI*row/latitudeSteps;
        const lat1=-Math.PI/2+Math.PI*(row+1)/latitudeSteps;
        const lon0=-Math.PI+2*Math.PI*column/longitudeSteps;
        const lon1=-Math.PI+2*Math.PI*(column+1)/longitudeSteps;
        for(const [lat,lon] of [[lat0,lon0],[lat1,lon0],[lat1,lon1],
          [lat0,lon0],[lat1,lon1],[lat0,lon1]])vertices.push(...point(lat,lon));
      }
      draw(scene);
    }
    canvas.style.touchAction='none';canvas.addEventListener('pointerdown',event=>{
      drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',event=>{
      if(!drag)return;yaw+=(event.clientX-drag[0])*.007;
      pitch=Math.max(-1.45,Math.min(1.45,pitch+(event.clientY-drag[1])*.007));
      drag=[event.clientX,event.clientY];draw(global.currentPlanetExplorationScene);
    });
    for(const name of ['pointerup','pointercancel','lostpointercapture']){
      canvas.addEventListener(name,()=>{drag=null;});
    }
    return {load,draw};
  }
  global.createPlanetGlobe=create;
})(window);
