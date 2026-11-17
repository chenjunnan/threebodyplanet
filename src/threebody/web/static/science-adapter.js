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
    bodyIds:IDS,validateTrajectory,matchLinkedArchive,
    atIndex,positionAt,nearestRealThermal,
  });
})(window);
