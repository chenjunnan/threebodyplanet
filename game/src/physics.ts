export type Vec3 = [number, number, number];
export interface TerrainMode { direction: Vec3; phase: number; frequency: number; amplitude: number; }
export interface TerrainModel {continent_scale:number;mountain_strength:number;fault_strength:number;canyon_strength:number;plain_fraction:number;ridge_directions:Vec3[];fault_normals:Vec3[]}
export const dot=(a:Vec3,b:Vec3)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
export const length=(a:Vec3)=>Math.hypot(...a);
export const normalize=(a:Vec3):Vec3=>{const n=length(a);return[a[0]/n,a[1]/n,a[2]/n]};
export const cross=(a:Vec3,b:Vec3):Vec3=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
export const add=(a:Vec3,b:Vec3):Vec3=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
export const sub=(a:Vec3,b:Vec3):Vec3=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
export const scale=(a:Vec3,s:number):Vec3=>[a[0]*s,a[1]*s,a[2]*s];
export function geodeticUnit(latD:number,lonD:number):Vec3{const a=latD*Math.PI/180,b=lonD*Math.PI/180;return[Math.cos(a)*Math.cos(b),Math.cos(a)*Math.sin(b),Math.sin(a)]}
export function unitToGeodetic(v:Vec3){const n=normalize(v);return{latitudeDeg:Math.asin(n[2])*180/Math.PI,longitudeDeg:Math.atan2(n[1],n[0])*180/Math.PI}}
// Exact port of the archived continuous spherical terrain contract.
export function terrainHeight(n0:Vec3,modes:TerrainMode[],m:TerrainModel,relief:number,exponent:number):number{
 const n=normalize(n0);let base=0;for(const x of modes)base+=x.amplitude*Math.sin(x.frequency*dot(n,x.direction)+x.phase);
 const ridges=(m.ridge_directions??[]).reduce((s,r)=>s+Math.pow(1-Math.abs(dot(n,r)),5),0);
 const faults=(m.fault_normals??[]).reduce((s,f)=>s+Math.exp(-Math.pow(dot(n,f)/.025,2)),0);
 const canyon=(m.fault_normals??[]).reduce((s,f)=>s+Math.exp(-Math.pow(dot(n,f)/.010,2)),0);
 const shaped=Math.sign(base)*Math.pow(Math.abs(base),exponent);
 const raw=shaped*m.continent_scale+ridges*m.mountain_strength+faults*m.fault_strength-canyon*m.canyon_strength;
 return Math.max(-1.25,Math.min(1.25,raw))*relief;
}
export interface LocalFrame{up:Vec3;east:Vec3;north:Vec3;originEcef:Vec3}
export function localFrame(lat:number,lon:number,r:number,h:number):LocalFrame{const up=geodeticUnit(lat,lon);let east=cross([0,0,1],up);if(length(east)<1e-9)east=[0,1,0];east=normalize(east);return{up,east,north:normalize(cross(up,east)),originEcef:scale(up,r+h)}}
export function localToEcef(f:LocalFrame,e:number,u:number,n:number):Vec3{return add(f.originEcef,add(scale(f.east,e),add(scale(f.up,u),scale(f.north,n))))}
export function ecefToLocal(f:LocalFrame,p:Vec3):Vec3{const d=sub(p,f.originEcef);return[dot(d,f.east),dot(d,f.up),dot(d,f.north)]}
export function stellarAngularRadius(r:number,d:number){if(!(r>0&&d>=r))throw Error('invalid stellar geometry');return Math.asin(r/d)}
export function rotateAroundAxis(v:Vec3,a0:Vec3,q:number):Vec3{const a=normalize(a0),c=Math.cos(q),s=Math.sin(q);return add(add(scale(v,c),scale(cross(a,v),s)),scale(a,dot(a,v)*(1-c)))}
export function planetSurfaceBasis(axis0:Vec3,latDeg:number,lonDeg:number,angle:number){
 const axis=normalize(axis0),reference:Vec3=Math.abs(axis[0])<.9?[1,0,0]:[0,1,0];
 const e0=normalize(sub(reference,scale(axis,dot(reference,axis)))),e1=cross(axis,e0);
 const lat=latDeg*Math.PI/180,lon=lonDeg*Math.PI/180+angle;
 const radial=add(scale(e0,Math.cos(lon)),scale(e1,Math.sin(lon)));
 const up=normalize(add(scale(radial,Math.cos(lat)),scale(axis,Math.sin(lat))));
 const east=normalize(add(scale(e0,-Math.sin(lon)),scale(e1,Math.cos(lon))));
 return{up,east,north:normalize(cross(up,east))};
}
