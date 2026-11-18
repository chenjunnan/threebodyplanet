/* Local first-person terrain view. Geometry is a parameter experiment, not an archive. */
(function(global){
  'use strict';
  function starSpriteMetrics(angularRadius,canvasHeight,fov,minimumPsf=5){
    const verticalFov=2*Math.atan(1/fov);
    const diameterPixels=2*angularRadius/verticalFov*canvasHeight;
    return {diameterPixels,pointSize:Math.max(minimumPsf,Math.min(96,diameterPixels)),
      pointSource:diameterPixels<minimumPsf};
  }
  global.planetStarSpriteMetrics=starSpriteMetrics;
  function heightAt(scene,lat,lon){
    const n=[Math.cos(lat)*Math.cos(lon),Math.cos(lat)*Math.sin(lon),Math.sin(lat)];
    const raw=scene.terrain_modes.reduce((sum,m)=>sum+
      Math.sin(n.reduce((v,x,k)=>v+x*m.direction[k],0)*m.frequency+m.phase)*m.amplitude,0);
    return scene.parameters.relief_m*Math.sign(raw)*
      Math.abs(raw)**scene.parameters.terrain_shape_exponent;
  }
  global.planetSurfaceHeight=heightAt;
  function create(canvas){
    const gl=canvas.getContext('webgl2',{antialias:true,preserveDrawingBuffer:true});
    if(!gl)return null;
    const compile=(type,source)=>{
      const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);
      if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const vs=compile(gl.VERTEX_SHADER,`#version 300 es
      in vec3 position;in vec3 color;out vec3 shade;out vec3 worldPos;
      uniform float yaw;uniform float pitch;uniform float aspect;uniform float fov;uniform float pointSize;uniform bool sky;uniform bool pointSource;
      void main(){
        vec3 p=vec3(cos(yaw)*position.x+sin(yaw)*position.z,position.y,
          -sin(yaw)*position.x+cos(yaw)*position.z);
        p=vec3(p.x,cos(pitch)*p.y+sin(pitch)*p.z,-sin(pitch)*p.y+cos(pitch)*p.z);
        float w=-p.z;
        // Standard finite perspective depth for terrain. The former constant
        // far-plane expression let triangles crossing the camera plane expand
        // across the sky when tracking a high-altitude star.
        float nearPlane=.002,farPlane=1000.0;
        float terrainDepth=(farPlane+nearPlane)/(nearPlane-farPlane)*p.z+
          (2.0*farPlane*nearPlane)/(nearPlane-farPlane);
        gl_Position=vec4(p.x*fov/aspect,p.y*fov,sky?w*.999:terrainDepth,w);
        gl_PointSize=pointSize;shade=color;worldPos=position;
      }`);
    const fs=compile(gl.FRAGMENT_SHADER,`#version 300 es
      precision highp float;in vec3 shade;in vec3 worldPos;out vec4 result;uniform bool sky;uniform bool pointSource;
      float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float noise(vec2 p){
        vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
        return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),
          mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);
      }
      void main(){
        if(sky){
          float radius=length(gl_PointCoord-.5);
          if(radius>=.5)discard;
          // Unresolved stars use an anti-aliased point-spread proxy. Resolved
          // discs keep a sharper limb. Neither changes the physical angle.
          float alpha=pointSource?1.0-smoothstep(.18,.5,radius):
            1.0-smoothstep(.44,.5,radius);
          if(alpha<=.001)discard;
          float core=pointSource?1.0+.35*exp(-24.0*radius*radius):1.0;
          result=vec4(min(vec3(1.0),shade*core),alpha);
        }
        else{
          vec2 uv=worldPos.xz*100000.0;
          float scale=max(0.25,length(fwidth(uv)));
          float grit=noise(uv/scale);
          float stones=noise(uv/max(1.5,scale));
          vec3 surface=shade*(.85+.1*stones+.05*grit);
          float fog=1.0-exp(-length(worldPos.xz)*.4);
          result=vec4(mix(surface,vec3(.12,.16,.20),fog*.65),1.0);
        }
      }`);
    const program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);
    gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
    const buffer=gl.createBuffer();let terrain=[],decorativeStars=[],realStars=[],yaw=0,pitch=-.12,drag=null;
    let lost=false,fov=1.4,skyColor=[.025,.045,.09],lockedStar='';
    const loc=name=>gl.getUniformLocation(program,name);
    function draw(){
      if(lost)return;
      gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(...skyColor,1);
      gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);
      gl.useProgram(program);gl.uniform1f(loc('yaw'),yaw);gl.uniform1f(loc('pitch'),pitch);
      gl.uniform1f(loc('aspect'),canvas.width/canvas.height);gl.uniform1f(loc('fov'),fov);
      gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      const batches=[[terrain,false,1,false],[decorativeStars,true,3,true],
        ...realStars.map(star=>[star.data,true,star.pointSize,star.pointSource])];
      for(const [data,sky,size,pointSource] of batches){
        if(!data.length)continue;
        if(sky){gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);}
        else gl.disable(gl.BLEND);
        gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(data),gl.DYNAMIC_DRAW);
        for(const [name,offset] of [['position',0],['color',12]]){
          const p=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(p);
          gl.vertexAttribPointer(p,3,gl.FLOAT,false,24,offset);
        }
        gl.uniform1f(loc('pointSize'),size);
        gl.uniform1i(loc('sky'),sky?1:0);gl.uniform1i(loc('pointSource'),pointSource?1:0);
        gl.drawArrays(sky?gl.POINTS:gl.TRIANGLES,0,data.length/6);
      }
    }
    function orient(scene){
      const star=scene?.sky?.find(item=>item.id===lockedStar&&item.above_geometric_horizon);
      if(!star)return;
      const az=star.azimuth_deg*Math.PI/180,alt=star.altitude_deg*Math.PI/180;
      const x=Math.cos(alt)*Math.sin(az),y=Math.sin(alt),z=-Math.cos(alt)*Math.cos(az);
      // Select the branch that keeps the target in front of the camera (-Z).
      // The former atan2 branch rotated it behind the view and left a clipped
      // point sprite at the upper edge for high-altitude stars.
      yaw=Math.atan2(x,-z);
      const rotatedZ=-Math.sin(yaw)*x+Math.cos(yaw)*z;
      pitch=Math.atan2(y,-rotatedZ);
    }
    function load(scene){
      terrain=[];decorativeStars=[];realStars=[];
      if(!scene){skyColor=[.025,.045,.09];draw();return;}
      const optical=scene.atmospheric_optics;
      const intensity=optical?.sky_scattered_intensity_proxy_w_m2||0;
      const exposure=1-Math.exp(-intensity/500);
      skyColor=(optical?.sky_display_srgb||[0,0,0]).map(v=>.012+v*.48*exposure);
      const p=scene.parameters,phi=p.latitude_deg*Math.PI/180,lambda=p.longitude_deg*Math.PI/180;
      const height=(lat,lon)=>heightAt(scene,lat,lon);
      const base=scene.observer_ground_level_m+p.observer_height_m;
      const point=(x,z)=>{
        const d=scene.radius_m*Math.min(.04,.3*Math.cos(phi));
        const lat=phi-z*d/scene.radius_m;
        const lon=lambda+x*d/(scene.radius_m*Math.max(.1,Math.cos(phi)));
        const h=height(lat,lon),water=scene.has_surface_water&&
          scene.water_surface_visible&&h<scene.sea_level_m;
        const surface=water?scene.sea_level_m:h;
        const exaggeration=scene.visualization?.terrain_vertical_exaggeration||1;
        const y=((surface-scene.observer_ground_level_m)*exaggeration+
          scene.observer_ground_level_m-base-(x*x+z*z)*d*d/(2*scene.radius_m))/1e5;
        const snow=scene.observer_temperature_proxy_k<273.16;
        const dx=(height(lat,lon+1e-5)-height(lat,lon-1e-5))/(2e-5*scene.radius_m*Math.max(.1,Math.cos(lat)));
        const dz=(height(lat-1e-5,lon)-height(lat+1e-5,lon))/(2e-5*scene.radius_m);
        const length=Math.hypot(dx,1,dz),normal=[-dx/length,1/length,-dz/length];
        const total=Math.max(1e-12,scene.sky.reduce((sum,s)=>sum+s.irradiance_w_m2,0));
        let light=scene.pressure_pa>0?.10:.035;
        for(const star of scene.sky){
          const a=star.azimuth_deg*Math.PI/180,b=star.altitude_deg*Math.PI/180;
          const direction=[Math.cos(b)*Math.sin(a),Math.sin(b),-Math.cos(b)*Math.cos(a)];
          if(star.above_geometric_horizon)light+=.84*Math.max(0,
            normal.reduce((sum,v,k)=>sum+v*direction[k],0))*star.irradiance_w_m2/total;
        }
        const grain=.9+.1*Math.sin(lat*20000+Math.sin(lon*17000));
        const shade=Math.sqrt(Math.max(0,light))*grain;
        const c=water?[.03*shade,.24*shade,.38*shade]:snow?[.7*shade,.75*shade,.76*shade]:
          [.42*shade,.35*shade,.24*shade];
        return [x*d/1e5,y,z*d/1e5,...c];
      };
      const n=64;
      for(let j=0;j<n;j++)for(let i=0;i<n;i++){
        const x=-1+2*i/n,z=-1+2*j/n,step=2/n;
        for(const [dx,dz] of [[0,0],[step,0],[step,step],[0,0],[step,step],[0,step]]){
          terrain.push(...point((x+dx)**3,(z+dz)**3));
        }
      }
      // Decorative star field is explicitly separate from the three archived stars.
      if(scene.visualization?.background_stars_visible!==false)for(let i=0;i<150;i++){
        const az=i*2.399963,alt=Math.asin((i+.5)/150);
        decorativeStars.push(100*Math.cos(alt)*Math.sin(az),100*Math.sin(alt),
          -100*Math.cos(alt)*Math.cos(az),.25,.3,.4);
      }
      for(const star of scene.sky){
        if(!star.above_geometric_horizon)continue;
        const az=star.azimuth_deg*Math.PI/180,alt=star.altitude_deg*Math.PI/180;
        const irradiance=star.surface_direct_irradiance_proxy_w_m2;
        if(irradiance<=1e-9)continue;
        const exposure=1-Math.exp(-irradiance/500);
        const transmitted=star.visible_transmittance_rgb;
        const raw=star.blackbody_rgb.map((value,i)=>value*transmitted[i]);
        const peak=Math.max(...raw);
        if(peak<=1e-12)continue;
        const color=raw.map(value=>Math.min(1,value/peak*(.25+.75*exposure)));
        const minimumPsf=scene.visualization?.minimum_point_source_psf_px||5;
        const sprite=starSpriteMetrics(star.angular_radius_rad,canvas.height,fov,minimumPsf);
        realStars.push({pointSize:sprite.pointSize,
          pointSource:sprite.pointSource,data:[
          99*Math.cos(alt)*Math.sin(az),99*Math.sin(alt),
          -99*Math.cos(alt)*Math.cos(az),...color]});
      }
      orient(scene);
      draw();
    }
    canvas.style.touchAction='none';
    canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointermove',e=>{
      if(!drag)return;yaw+=(e.clientX-drag[0])*.006;
      pitch=Math.max(-1.4,Math.min(1.4,pitch+(e.clientY-drag[1])*.006));
      drag=[e.clientX,e.clientY];draw();
    });
    for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,()=>{drag=null;});
    canvas.addEventListener('wheel',e=>{e.preventDefault();fov=Math.max(.7,Math.min(4,fov*Math.exp(-e.deltaY*.001)));draw();},{passive:false});
    canvas.addEventListener('keydown',e=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(e.key))return;
      e.preventDefault();
      if(e.key==='Home'){yaw=0;pitch=-.12;}
      else if(e.key==='ArrowLeft')yaw-=.1;else if(e.key==='ArrowRight')yaw+=.1;
      else pitch=Math.max(-1.4,Math.min(1.4,pitch+(e.key==='ArrowUp'?.1:-.1)));
      draw();
    });
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();lost=true;
      document.querySelector('#planet-sky').textContent+=' · 地表WebGL上下文丢失，请刷新恢复；地图与数值仍可使用。';});
    function lockStar(id,scene){lockedStar=id||'';orient(scene);draw();}
    return {load,draw,lockStar};
  }
  global.createPlanetGround=create;
})(window);
