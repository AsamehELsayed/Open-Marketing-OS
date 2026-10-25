'use strict';
const {URL}=require('node:url');
const HEADER='x-omos-desktop-token';
function exactOrigin(url, origin){try{return new URL(url).origin===origin;}catch{return false;}}
function requestHeaders(details, state){
 const headers={...details.requestHeaders}; for(const key of Object.keys(headers))if(key.toLowerCase()===HEADER)delete headers[key];
 const mainFrame=!!details.frame && details.frame===details.webContents?.mainFrame;
 const trusted=mainFrame && state.origin && details.webContentsId===state.contentsId && exactOrigin(details.url,state.origin) && !details.url.startsWith('ws');
 if(trusted)headers[HEADER]=state.token;
 return {cancel:!trusted,requestHeaders:headers};
}
function validReady(value,expected){return value && value.type==='ready' && value.protocol===1 && value.pid===expected.pid && value.session_id===expected.session_id && value.build_id===expected.build_id && value.edition===expected.edition && value.version===expected.version && Number.isInteger(value.port)&&value.port>0&&value.port<65536;}
function externalURL(raw){try{if(raw.length>2048)return null;const u=new URL(raw); if(u.protocol!=='https:'||u.username||u.password||u.port)return null;const hosts=['docs.openmarketingos.com','openrouter.ai','huggingface.co','github.com'];return hosts.includes(u.hostname)?u.href:null;}catch{return null;}}
function encodeFrame(value){const b=Buffer.from(JSON.stringify(value));if(b.length>16384)throw Error('oversize frame');const h=Buffer.alloc(4);h.writeUInt32BE(b.length);return Buffer.concat([h,b]);}
class Frames{constructor(onFrame,onError){this.buffer=Buffer.alloc(0);this.onFrame=onFrame;this.onError=onError;this.failed=false;}push(chunk){if(this.failed)return;try{this.buffer=Buffer.concat([this.buffer,chunk]);while(this.buffer.length>=4){const n=this.buffer.readUInt32BE(0);if(n<2||n>16384)throw Error('invalid frame');if(this.buffer.length<4+n)return;const v=JSON.parse(this.buffer.subarray(4,4+n).toString('utf8'));this.buffer=this.buffer.subarray(4+n);this.onFrame(v);}}catch{this.failed=true;this.onError(Error('invalid sidecar protocol'));}}}
module.exports={HEADER,exactOrigin,requestHeaders,validReady,externalURL,encodeFrame,Frames};
