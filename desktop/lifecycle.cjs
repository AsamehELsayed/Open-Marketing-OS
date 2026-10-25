'use strict';
const {spawn}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const {join}=require('node:path');
const {Frames,encodeFrame,validReady,HEADER}=require('./security.cjs');
function cleanEnv(source){const out={};for(const k of ['SystemRoot','WINDIR','PATH','TEMP','TMP','HOME','USERPROFILE','LOCALAPPDATA','APPDATA','XDG_DATA_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG','LC_ALL'])if(source[k])out[k]=source[k];return out;}
class Sidecar{
 constructor(manifest,resources,onExit,dependencies={}){this.spawn=dependencies.spawn||spawn;this.fetch=dependencies.fetch||fetch;this.manifest=manifest;this.resources=resources;this.onExit=onExit;this.child=null;this.origin=null;this.token=null;}
 async start(){
 this.groupReady=false;const session_id=randomBytes(24).toString('hex');this.token=randomBytes(32).toString('hex');
 const executable=join(this.resources,'backend',process.platform==='win32'?'omos-sidecar.exe':'omos-sidecar');
 const env={...cleanEnv(process.env),OMOS_FROZEN:'1',OMOS_BUNDLE_DIR:join(this.resources,'backend','_internal')};
 const child=this.child=this.spawn(executable,[],{env,stdio:['pipe','pipe','pipe'],windowsHide:true,detached:false});
 child.stderr.on('data',()=>{}); // Never forward backend diagnostics that could contain user data.
 child.once('exit',()=>{this.origin=null;this.token=null;this.onExit?.();});
 const expected={...this.manifest,pid:child.pid,session_id};
 try{const ready=await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('Sidecar startup timed out')),90000);const finish=(fn,v)=>{clearTimeout(timeout);fn(v);};child.once('error',()=>finish(reject,Error('Sidecar could not start')));child.once('exit',()=>finish(reject,Error('Sidecar exited during startup')));const frames=new Frames(v=>{if(!validReady(v,expected))return finish(reject,Error('Sidecar identity mismatch'));finish(resolve,v);},e=>finish(reject,e));child.stdout.on('data',chunk=>frames.push(chunk));child.stdin.write(encodeFrame({protocol:1,session_id,token:this.token,build_id:this.manifest.build_id,edition:this.manifest.edition}));});
 this.groupReady=true;this.origin=`http://127.0.0.1:${ready.port}`;
 const nonce=randomBytes(24).toString('hex');const response=await this.fetch(`${this.origin}/desktop/ready?nonce=${nonce}`,{headers:{[HEADER]:this.token},signal:AbortSignal.timeout(5000),redirect:'error'});const proof=await response.json();
 if(!response.ok||proof.ready!==true||proof.nonce!==nonce||!validReady({...proof,type:'ready',port:ready.port},expected))throw Error('Sidecar challenge failed');return this.origin;
 }catch(e){await this.stop();throw e;}
 }
 async stop(){const child=this.child;const origin=this.origin,token=this.token;this.origin=null;this.token=null;if(!child)return;this.child=null;
 if(origin&&token)try{await this.fetch(`${origin}/desktop/shutdown`,{method:'POST',headers:{[HEADER]:token},signal:AbortSignal.timeout(1500)});}catch{}
 child.stdin.end();if(child.exitCode!==null)return;
 await new Promise(resolve=>{const timer=setTimeout(()=>{try{if(process.platform==='win32'||!this.groupReady)child.kill();else process.kill(-child.pid,'SIGKILL');}catch{}resolve();},5000);child.once('exit',()=>{clearTimeout(timer);resolve();});});
 }
}
module.exports={Sidecar,cleanEnv};

