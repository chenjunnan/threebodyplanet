/* M6.3-d / surface-v1. Local illustrative materials, never scientific terrain. */
(function(global){
  'use strict';
  const colors=[[243,164,74],[226,116,89],[139,183,255],[156,143,125]];
  const cache=new Map();
  const clamp=x=>Math.max(0,Math.min(1,x));
  function noise(x,y,z){
    const ix=Math.floor(x),iy=Math.floor(y),iz=Math.floor(z);
    const smooth=t=>t*t*(3-2*t);
    const sx=smooth(x-ix),sy=smooth(y-iy),sz=smooth(z-iz);
    const hash=(a,b,c)=>{const v=Math.sin(a*127.1+b*311.7+c*74.7)*43758.5453;return v-Math.floor(v);};
    let value=0;
    for(let a=0;a<2;a++)for(let b=0;b<2;b++)for(let c=0;c<2;c++){
      value+=hash(ix+a,iy+b,iz+c)*(a?sx:1-sx)*(b?sy:1-sy)*(c?sz:1-sz);
    }
    return value;
  }
  // Same blue -> red scale as the C1 time/latitude plot, in Kelvin.
  function temperatureColor(k,min,max){
    const h=(240-240*clamp((k-min)/Math.max(max-min,1e-12)))/60;
    const c=.85*(1-Math.abs(2*.54-1)),x=c*(1-Math.abs(h%2-1)),m=.54-c/2;
    const rgb=h<1?[c,x,0]:h<2?[x,c,0]:h<3?[0,c,x]:[0,x,c];
    return rgb.map(v=>Math.round((v+m)*255));
  }
  function thermalFrame(thermal,time){
    if(!Number.isFinite(time)||!thermal?.rows?.length)return null;
    const rows=thermal.rows;
    if(time<rows[0].t_s||time>rows[rows.length-1].t_s)return null;
    const match=global.ThreeBodyScienceAdapter.nearestRealThermal(thermal,time);
    const row=match?.row,temps=row?.c1_temperature_k,lats=row?.c1_latitude_rad;
    if(!Array.isArray(temps)||!Array.isArray(lats)||!temps.length||temps.length>512
      ||temps.length!==lats.length)return null;
    if(!temps.every(t=>Number.isFinite(t)&&t>=0))return null;
    if(!lats.every((lat,i)=>Number.isFinite(lat)&&
      Math.abs(Math.sin(lat)-(-1+(2*i+1)/temps.length))<1e-7))return null;
    // Stable across the loaded window; no frame-by-frame colour exaggeration.
    const all=rows.flatMap(r=>Array.isArray(r.c1_temperature_k)?r.c1_temperature_k:[]);
    if(!all.every(t=>Number.isFinite(t)&&t>=0))return null;
    const min=all.reduce((a,b)=>Math.min(a,b),Infinity);
    const max=all.reduce((a,b)=>Math.max(a,b),-Infinity);
    return Object.freeze({row,delta_s:match.delta_s,min,max});
  }
  function bandAt(y,bands){return Math.max(0,Math.min(bands-1,Math.floor((1-y)*bands/2)));}
  function disk(body,size=128,frame=null){
    const key=body+':'+size;
    if(!frame&&cache.has(key))return cache.get(key);
    const canvas=document.createElement('canvas');canvas.width=canvas.height=size;
    const ctx=canvas.getContext('2d'),image=ctx.createImageData(size,size);
    for(let py=0;py<size;py++)for(let px=0;px<size;px++){
      const x=2*(px+.5)/size-1,y=2*(py+.5)/size-1,r2=x*x+y*y;
      if(r2>1)continue;
      const z=Math.sqrt(1-r2),index=(py*size+px)*4;
      let color;
      if(frame){
        const band=bandAt(y,frame.row.c1_temperature_k.length);
        color=temperatureColor(frame.row.c1_temperature_k[band],frame.min,frame.max);
      }else{
        // Deterministic three-dimensional noise on a unit sphere, independent of time.
        const grain=noise(46*x+body*17,46*y,46*z);
        const terrain=.6*noise(8*x+19,8*y,8*z)+.4*noise(22*x,22*y,22*z);
        const spots=noise(12*x,12*y+body*9,12*z);
        const light=body<3 ? (.68+.3*z)*(.68+.32*grain)*(.86+.14*spots) :
          (.36+.64*Math.max(0,.3*x-.4*y+.85*z))*(.5+.35*terrain+.15*grain);
        color=colors[body].map(c=>Math.round(c*light));
      }
      image.data.set([...color,255],index);
    }
    ctx.putImageData(image,0,0);
    if(!frame)cache.set(key,canvas);
    return canvas;
  }
  global.ThreeBodySurfaces=Object.freeze({version:'surface-v1',disk,thermalFrame,bandAt,temperatureColor});
})(window);
