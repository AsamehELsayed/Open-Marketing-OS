'use strict';
const {app,BrowserWindow,session,dialog,shell,ipcMain}=require('electron');
const path=require('node:path');
const {Sidecar}=require('./lifecycle.cjs');
const {requestHeaders,exactOrigin,externalURL}=require('./security.cjs');
const manifest=require('./build-manifest.json');
let window,backend,quitting=false,starting=false,recovering=false;
const state={origin:null,token:null,contentsId:null};
function invalidate(){state.origin=null;state.token=null;}
async function recover(message){if(quitting||starting||recovering)return;recovering=true;invalidate();if(window){window.removeAllListeners('closed');window.destroy();}window=null;const result=await dialog.showMessageBox({type:'error',title:'Open Marketing OS',message,buttons:['Retry','Quit'],defaultId:0,cancelId:1});if(result.response===0){recovering=false;await launch();}else app.quit();}
async function launch(){if(starting||quitting)return;starting=true;invalidate();if(backend)await backend.stop();
 try{backend=new Sidecar(manifest,process.resourcesPath,()=>{if(!starting&&!quitting)void recover('The local service stopped. Your saved data is preserved.');});await backend.start();
 const dedicated=session.fromPartition(`omos-${require('node:crypto').randomUUID()}`,{cache:false});
 window=new BrowserWindow({width:1280,height:860,minWidth:880,minHeight:640,show:false,webPreferences:{session:dedicated,preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,devTools:false,webviewTag:false}});
 Object.assign(state,{origin:backend.origin,token:backend.token,contentsId:window.webContents.id});
 dedicated.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));dedicated.setPermissionCheckHandler(()=>false);dedicated.setDevicePermissionHandler(()=>false);
 dedicated.webRequest.onBeforeRequest((details,callback)=>{const local=exactOrigin(details.url,state.origin);const blob=details.url.startsWith(`blob:${state.origin}/`);callback({cancel:details.webContentsId!==state.contentsId||(!local&&!blob)||details.url.startsWith('ws')||details.resourceType==='serviceWorker'});});
 dedicated.webRequest.onBeforeSendHeaders((details,callback)=>callback(requestHeaders(details,state)));
 dedicated.on('will-download',async(event,item,contents)=>{if(contents!==window?.webContents){event.preventDefault();return;}const url=item.getURL();
 if(!url.startsWith(`blob:${state.origin}/`)||!/\.(md|zip)$/i.test(item.getFilename())||Date.now()-(window._lastGesture||0)>15000||item.getTotalBytes()>25*1024*1024){event.preventDefault();return;}
 item.pause();const result=await dialog.showSaveDialog(window,{defaultPath:path.basename(item.getFilename())});if(result.canceled||!result.filePath){item.cancel();return;}item.setSavePath(result.filePath);item.on('updated',()=>{if(item.getReceivedBytes()>25*1024*1024)item.cancel();});item.resume();});
 window.webContents.on('before-input-event',(_e,input)=>{if(input.type==='keyDown')window._lastGesture=Date.now();});window.webContents.on('input-event',(_e,input)=>{if(input.type==='mouseDown')window._lastGesture=Date.now();});
 window.webContents.setWindowOpenHandler(({url})=>{const safe=externalURL(url);if(safe)void dialog.showMessageBox(window,{type:'question',message:'Open this HTTPS link in your browser?',detail:safe,buttons:['Cancel','Open browser'],defaultId:0,cancelId:0}).then(result=>{if(result.response===1)void shell.openExternal(safe);});return {action:'deny'};});
 window.webContents.on('will-navigate',(event,url)=>{if(!exactOrigin(url,state.origin))event.preventDefault();});window.webContents.on('will-redirect',(event,url)=>{if(!exactOrigin(url,state.origin))event.preventDefault();});
 window.webContents.on('will-frame-navigate',details=>{if(!details.isMainFrame||!exactOrigin(details.url,state.origin))details.preventDefault();});
 window.webContents.on('will-attach-webview',event=>event.preventDefault());window.webContents.on('render-process-gone',()=>void recover('The application window stopped. Restart it to continue.'));
 window.once('ready-to-show',()=>window?.show());window.on('closed',()=>{window=null;if(!starting&&!quitting)app.quit();});await window.loadURL(`${state.origin}/app`);
 }catch{await backend?.stop();starting=false;await recover('Open Marketing OS could not start its bundled local service.');return;}starting=false;}
if(!app.requestSingleInstanceLock())app.quit();else{
 app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.show();window.focus();}});
 ipcMain.handle('desktop:status',event=>{if(event.sender.id!==state.contentsId||event.senderFrame!==event.sender.mainFrame||!exactOrigin(event.senderFrame.url,state.origin))throw Error('Unauthorized');return {version:manifest.version,edition:manifest.edition,connected:!!state.origin};});
 app.whenReady().then(launch);app.on('window-all-closed',()=>{if(!recovering)app.quit();});
 app.on('before-quit',event=>{if(quitting)return;event.preventDefault();quitting=true;invalidate();void backend?.stop().finally(()=>app.quit());});
 process.on('uncaughtException',()=>{invalidate();void backend?.stop().finally(()=>app.exit(1));});process.on('SIGTERM',()=>app.quit());process.on('SIGINT',()=>app.quit());}
