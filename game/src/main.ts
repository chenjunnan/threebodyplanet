import './style.css';
import {start} from './app';

const root=document.querySelector<HTMLElement>('#app');
if(!root)throw Error('game root missing');
void start(root);
