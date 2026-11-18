/* M7.1 shared Web core: authoritative JSON transport, state, and route lifecycle. */
(function(global){
  'use strict';
  const values=new Map(),listeners=new Map(),routeListeners=new Map();
  async function request(path,options={}){
    const response=await fetch(path,options);
    let result;
    try{result=await response.json();}
    catch(error){throw new Error('服务端返回非JSON响应：'+response.status);}
    if(!response.ok)throw new Error(result.message||result.error||String(response.status));
    return result;
  }
  function get(path){return request(path);}
  function post(path,payload){return request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}
  function set(key,value){values.set(key,value);for(const fn of listeners.get(key)||[])fn(value);}
  function getState(key){return values.get(key);}
  function subscribe(key,fn){if(!listeners.has(key))listeners.set(key,new Set());listeners.get(key).add(fn);return()=>listeners.get(key)?.delete(fn);}
  function onRoute(id,handlers){
    if(!routeListeners.has(id))routeListeners.set(id,new Set());
    routeListeners.get(id).add(handlers);
    if(activeRoute===id)handlers.enter?.();
    return()=>routeListeners.get(id)?.delete(handlers);
  }
  let activeRoute=null;
  function route(id){
    if(activeRoute===id)return;
    for(const item of routeListeners.get(activeRoute)||[])item.leave?.();
    activeRoute=id;set('route',id);
    for(const item of routeListeners.get(id)||[])item.enter?.();
  }
  global.ThreeBodyWeb=Object.freeze({get,post,set,getState,subscribe,onRoute,route});
})(window);
