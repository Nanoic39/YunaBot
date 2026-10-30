export const appearanceDefaults = Object.freeze({theme:'dark',style:'glass',layout:'sidebar',density:'comfortable',motion:'system'});
export const appearanceOptions = {
 theme:['dark','light','system'], style:['glass','plain'], layout:['sidebar','rail','top'], density:['comfortable','compact'], motion:['system','reduced'],
};
export const appearanceKey='yuna_next_appearance_v1';
export function normalizeAppearance(value){
 const clean={};
 for(const [key,fallback] of Object.entries(appearanceDefaults))clean[key]=appearanceOptions[key].includes(value?.[key])?value[key]:fallback;
 return clean;
}
export function loadAppearance(storage){try{return normalizeAppearance(JSON.parse(storage.getItem(appearanceKey)||'null'));}catch{return {...appearanceDefaults};}}
export function applyAppearance(value,root,systemDark=true){
 const clean=normalizeAppearance(value);
 root.dataset.theme=clean.theme==='system'?(systemDark?'dark':'light'):clean.theme;
 for(const key of ['style','layout','density','motion'])root.dataset[key]=clean[key];
 root.style.colorScheme=root.dataset.theme;
 return clean;
}
