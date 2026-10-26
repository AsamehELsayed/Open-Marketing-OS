const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
// main.cjs needs a live Electron runtime, so this guards the recovery contract at source level. The behaviour itself
// is proven end to end by the installed-app crash test (kill the sidecar, expect the app to stay up and offer Retry).
const source=fs.readFileSync(path.join(__dirname,'..','main.cjs'),'utf8');
test('recover() destroys the window without letting window-all-closed quit the app',()=>{
 assert.match(source,/recovering=true;invalidate\(\);if\(window\)\{window\.removeAllListeners\('closed'\);window\.destroy\(\)/);
 assert.match(source,/app\.on\('window-all-closed',\(\)=>\{if\(!recovering\)app\.quit\(\);\}\)/);
});
test('Retry clears the recovery flag before relaunching; Quit still quits',()=>{
 assert.match(source,/result\.response===0\)\{recovering=false;await launch\(\);\}else app\.quit\(\)/);
});
