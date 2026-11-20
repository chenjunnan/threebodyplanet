export interface PlayerSave{schema:'p1-player-save-v1';runId:string;sourceHash:string;latitudeDeg:number;longitudeDeg:number;heightM:number;camera:'first'|'third';savedAt:string}
export const saveKey=(id:string)=>'threebody:p1:player:'+id;
export function storePlayer(v:PlayerSave){localStorage.setItem(saveKey(v.runId),JSON.stringify(v))}
export function loadPlayer(id:string,hash:string):PlayerSave|null{try{const x=localStorage.getItem(saveKey(id));if(!x)return null;const v=JSON.parse(x)as PlayerSave;return v.schema==='p1-player-save-v1'&&v.runId===id&&v.sourceHash===hash?v:null}catch{return null}}
