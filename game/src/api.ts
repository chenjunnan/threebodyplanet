import type{TerrainMode,TerrainModel,Vec3}from'./physics';
import type{MaterialProfile}from'./surface-material';
export interface RunSummary{run_id:string;world_id:string;status:string;simulation_time_s:number;available_manifests:string[]}
export interface Body{id:string;kind:string;mass_kg:number;radius_m:number;rotation_rate_rad_s:number|null;rotation_axis_inertial_unit:Vec3|null;luminosity_w:number|null}
export interface TrajectoryPoint{t_s:number;positions_m:Record<string,Vec3>}
export interface Scenario{scenario_sha256:string;world_id:string;run_id:string;terrain_modes:TerrainMode[];terrain_model:TerrainModel;parameters:{relief_m:number;terrain_shape_exponent:number};sea_level_m:number;water_surface_visible?:boolean;radius_m:number;gravity_m_s2:number;pressure_pa:number;atmospheric_optics?:{sky_display_srgb?:[number,number,number]};environment_material_profile?:MaterialProfile;integrated_environment_result_sha256:string;integrated_environment_coverage_s:[number,number]}
export interface EnvironmentPoint{t_s:number;latitude_deg:number;longitude_deg:number;surface_temperature_k?:number;air_temperature_k?:number;surface_pressure_pa?:number;relative_humidity?:number;wind_east_m_s?:number;wind_north_m_s?:number;cloud_fraction?:number;precipitation_rate_kg_m2_s?:number;weather_events?:unknown[];surface_material?:unknown;[key:string]:unknown}
async function request<T>(url:string,init?:RequestInit):Promise<T>{const r=await fetch(url,init);if(!r.ok)throw Error(String(r.status)+' '+await r.text());return r.json()as Promise<T>}
export const listRuns=async()=>(await request<{worlds:RunSummary[]}>('/api/v1/game/worlds')).worlds;
export const bodies=(run:string)=>request<{initial_state_sha256:string;bodies:Body[]}>('/api/v1/runs/'+encodeURIComponent(run)+'/bodies');
export const trajectory=(run:string)=>request<{samples:TrajectoryPoint[];range_start_s:number;range_end_s:number;data_status:string}>('/api/v1/runs/'+encodeURIComponent(run)+'/trajectory?max_points=2000');
export const scenario=(run:string,t:number)=>request<Scenario>('/api/v1/game/worlds/'+encodeURIComponent(run)+'/scenario?t_s='+encodeURIComponent(String(t)));
export function environmentPoint(run:string,t:number,lat:number,lon:number){const q=new URLSearchParams({t_s:String(t),latitude_deg:String(lat),longitude_deg:String(lon)});return request<EnvironmentPoint>('/api/v1/environment/'+encodeURIComponent(run)+'/point?'+q)}
