const e=new Set;function n(s){for(const t of Array.from(e))try{t(s)}catch{}}function r(s){return e.add(s),()=>{e.delete(s)}}const c=r;export{n as p,c as s};
