/* M6.3-a: pure, read-only M5A science archive adapter. No network or physics. */
(function(global) {
  'use strict';
  const IDS=Object.freeze(['star-1','star-2','star-3','planet-1']);
  function ensure(ok, message) { if(!ok) throw new Error('M6.3 science: '+message); }
  function validateTrajectory(trajectory, expectedRunId) {
    ensure(trajectory && typeof trajectory==='object','trajectory missing');
    ensure(trajectory.run_id===expectedRunId, 'run ID mismatch');
    ensure(typeof trajectory.world_id==='string' && trajectory.world_id.length>0,
      'world ID missing');
    ensure(trajectory.units?.time==='s' && trajectory.units?.position==='m',
      'requires SI seconds and metres');
    ensure(trajectory.display_sampling==='uniform_record_index_no_interpolation',
      'unknown trajectory sampling semantics');
    ensure(Array.isArray(trajectory.samples) && trajectory.samples.length<=2000,
      'invalid bounded trajectory');
    ensure(trajectory.display_count===trajectory.samples.length,
      'display count mismatch');
    let previous=-Infinity;
    const samples=trajectory.samples.map((sample)=>{
      ensure(Number.isFinite(sample.t_s) && sample.t_s>previous,
        'non-increasing physical timestamps');
      previous=sample.t_s;
      ensure(sample.positions_m && typeof sample.positions_m==='object',
        'body positions missing');
      ensure(Object.keys(sample.positions_m).length===IDS.length,
        'unexpected body count');
      const positions_m={};
      for(const id of IDS) {
        const coords=sample.positions_m[id];
        ensure(Array.isArray(coords) && coords.length===3
          && coords.every(Number.isFinite),'non-finite or missing '+id);
        positions_m[id]=Object.freeze(coords.slice());
      }
      return Object.freeze({t_s:sample.t_s,positions_m:Object.freeze(positions_m)});
    });
    return Object.freeze({
      world_id:trajectory.world_id,run_id:trajectory.run_id,
      data_status:trajectory.data_status,
      samples:Object.freeze(samples),
      sample_count:trajectory.sample_count,
      display_count:trajectory.display_count,
      display_quality:'VERIFIED_OUTPUT_SAMPLES_ONLY',
      time_start_s:samples.length?samples[0].t_s:null,
      time_end_s:samples.length?samples[samples.length-1].t_s:null,
    });
  }
  function matchLinkedArchive(trajectory, linked, kind) {
    if(!linked) return null;
    ensure(linked.world_id===trajectory.world_id &&
      linked.run_id===trajectory.run_id,kind+' provenance world/run mismatch');
    if(kind==='environment') {
      ensure(Array.isArray(linked.rows),'environment samples unavailable');
      let prev=-Infinity;
      for(const row of linked.rows) {
        ensure(Number.isFinite(row.t_s) && row.t_s>prev,
          'environment timestamps invalid');
        prev=row.t_s;
      }
    }
    return linked;
  }
  function validateEvents(trajectory,linked){
    matchLinkedArchive(trajectory,linked,'events');
    ensure(Array.isArray(linked?.events)&&linked.events.length<=10000,'invalid event list');
    ensure(linked.event_count===linked.events.length,'event count mismatch');
    let previous=-Infinity;
    const events=linked.events.map(event=>{
      ensure(event&&typeof event==='object','invalid event record');
      ensure(Number.isFinite(event.t_s)&&event.t_s>=0&&event.t_s>=previous,'invalid event time');
      previous=event.t_s;
      ensure(['COLLISION','CLOSE_ENCOUNTER','ESCAPE_CANDIDATE'].includes(event.event_type),
        'unknown dynamics event');
      ensure(Array.isArray(event.body_ids)&&event.body_ids.length>0&&
        event.body_ids.every(id=>IDS.includes(id))&&
        new Set(event.body_ids).size===event.body_ids.length,'invalid event bodies');
      if(event.event_type==='COLLISION'&&trajectory.samples.length){
        ensure(trajectory.time_end_s<=event.t_s,'trajectory extends beyond collision');
      }
      return Object.freeze({...event,body_ids:Object.freeze(event.body_ids.slice())});
    });
    return Object.freeze(events);
  }
  function eventSample(samples,event){
    if(!samples.length||!Number.isFinite(event?.t_s)||
      event.t_s<samples[0].t_s||event.t_s>samples[samples.length-1].t_s)return null;
    let lo=0,hi=samples.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(samples[mid].t_s<event.t_s)lo=mid+1;else hi=mid;}
    const index=lo===0?0:lo===samples.length?lo-1:
      event.t_s-samples[lo-1].t_s<=samples[lo].t_s-event.t_s?lo-1:lo;
    return Object.freeze({index,delta_s:Math.abs(samples[index].t_s-event.t_s)});
  }
  function validateBodyMetadata(trajectory,metadata) {
    ensure(metadata && metadata.schema_version==='m6-3c-body-metadata-v1',
      'unsupported body metadata schema');
    ensure(metadata.run_id===trajectory.run_id &&
      metadata.world_id===trajectory.world_id,'body metadata run/world mismatch');
    ensure(/^[0-9a-f]{64}$/.test(metadata.initial_state_sha256||''),
      'body metadata provenance missing');
    ensure(metadata.coordinate_frame==='BARYCENTRIC_INERTIAL',
      'body metadata inertial frame mismatch');
    ensure(metadata.units?.mass==='kg' && metadata.units?.radius==='m',
      'body metadata SI units mismatch');
    ensure(Array.isArray(metadata.bodies) && metadata.bodies.length===IDS.length,
      'body metadata canonical count mismatch');
    const entries={};
    metadata.bodies.forEach((body,i)=>{
      ensure(body.id===IDS[i] && body.kind===(i<3?'STAR':'PLANET'),
        'body metadata canonical order/kind mismatch');
      ensure(Number.isFinite(body.mass_kg)&&body.mass_kg>0 &&
        Number.isFinite(body.radius_m)&&body.radius_m>0,
        'body metadata mass or radius invalid');
      for(const key of ['rotation_rate_rad_s','luminosity_w']){
        ensure(body[key]==null||(Number.isFinite(body[key])&&body[key]>=0),
          'body metadata '+key+' invalid');
      }
      if(body.rotation_axis_inertial_unit!=null){
        const a=body.rotation_axis_inertial_unit;
        ensure(Array.isArray(a)&&a.length===3&&a.every(Number.isFinite),
          'body axis invalid');
        ensure(Math.abs(Math.hypot(...a)-1)<1e-6,'body axis not normalized');
      }
      if(body.atmosphere_hypothesis!=null){
        ensure(i===3 && typeof body.atmosphere_hypothesis==='object',
          'atmosphere hypothesis is only valid for the planet');
        ensure(body.atmosphere_hypothesis.is_observation===false,
          'atmosphere prior must not claim observation');
        ensure(Number.isFinite(body.atmosphere_hypothesis.atmosphere_mass_kg)&&
          body.atmosphere_hypothesis.atmosphere_mass_kg>=0,
          'atmosphere prior mass invalid');
      }
      entries[body.id]=Object.freeze({
        ...body,
        rotation_axis_inertial_unit:body.rotation_axis_inertial_unit==null?
          null:Object.freeze(body.rotation_axis_inertial_unit.slice()),
      });
    });
    return Object.freeze({...metadata,bodies:Object.freeze(entries)});
  }
  function atIndex(trajectory,index) {
    ensure(Number.isInteger(index)&&index>=0&&index<trajectory.samples.length,
      'sample index out of verified range');
    return trajectory.samples[index];
  }
  function positionAt(trajectory,index,id) {
    ensure(IDS.includes(id),'unknown body');
    return atIndex(trajectory,index).positions_m[id].slice();
  }
  function nearestRealThermal(thermal,time_s) {
    if(!thermal?.rows?.length) return null;
    ensure(Number.isFinite(time_s),'time invalid');
    const rows=thermal.rows;
    let low=0,high=rows.length;
    while(low<high) {
      const mid=Math.floor((low+high)/2);
      if(rows[mid].t_s<time_s) low=mid+1;
      else high=mid;
    }
    const ix=low===0?0:low===rows.length?rows.length-1:
      Math.abs(rows[low].t_s-time_s)<Math.abs(rows[low-1].t_s-time_s)?
        low:low-1;
    return Object.freeze({
      row:rows[ix],delta_s:Math.abs(rows[ix].t_s-time_s),
      label:'NEAREST_VERIFIED_M3_SAMPLE_NOT_INTERPOLATED',
    });
  }
  global.ThreeBodyScienceAdapter=Object.freeze({
    bodyIds:IDS,validateTrajectory,validateEvents,eventSample,validateBodyMetadata,matchLinkedArchive,
    atIndex,positionAt,nearestRealThermal,
  });
})(window);
