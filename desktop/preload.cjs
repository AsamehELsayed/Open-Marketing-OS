'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('omosDesktop',Object.freeze({status:()=>ipcRenderer.invoke('desktop:status')}));
