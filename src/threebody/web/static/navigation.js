/* Route-level views share archived state; only one workspace is visible at a time. */
(function(){
  'use strict';
  const main=document.querySelector('main');
  const definitions=[['worlds','创建世界'],['tasks','任务与归档'],['orbit','轨道观察'],
    ['environment','科学环境'],['surface','行星地表'],['reports','比较报告'],['settings','高级配置']];
  const nav=document.createElement('nav');nav.className='workspace-nav';nav.setAttribute('aria-label','工作区导航');
  main.prepend(nav);
  const pages=Object.create(null);
  for(const [id,label] of definitions){
    const link=document.createElement('a');link.href='#/'+id;link.textContent=label;link.dataset.route=id;
    nav.append(link);
    const page=document.createElement('section');page.id='page-'+id;page.dataset.page=id;
    const heading=document.createElement('h2');heading.className='page-title';heading.textContent=label;
    page.append(heading);main.append(page);pages[id]=page;
  }
  const move=(selector,id)=>{const node=document.querySelector(selector);if(node)pages[id].append(node);};
  move('.hero','worlds');move('.world-builder','worlds');move('.runs','tasks');move('.task','tasks');
  move('.validator','settings');move('.experiment-comparison','reports');
  move('.thermal-panels','environment');move('.world-outcome-panel','environment');
  move('.integrated-environment-panel','environment');
  move('.surface-gallery','surface');move('.planet-exploration','surface');
  const observer=document.querySelector('.observer');
  const shared=document.createElement('section');shared.className='shared-timeline panel';
  shared.setAttribute('aria-label','当前归档与统一物理时间');
  for(const selector of ['.observe-filters','label[for="orbit-time"]','#orbit-time']){
    const node=observer.querySelector(selector);if(node)shared.append(node);
  }
  nav.after(shared);pages.orbit.append(observer);
  document.querySelector('.grid')?.remove();
  function show(){
    let id=location.hash.replace(/^#\//,'');if(!pages[id])id='worlds';
    for(const [key,page] of Object.entries(pages))page.hidden=key!==id;
    for(const link of nav.querySelectorAll('a')){
      if(link.dataset.route===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
    document.title=definitions.find(d=>d[0]===id)[1]+' · 三体星';
    window.ThreeBodyWeb?.route(id);
    window.dispatchEvent(new CustomEvent('threebody-route',{detail:id}));
  }
  window.addEventListener('hashchange',show);show();
})();
