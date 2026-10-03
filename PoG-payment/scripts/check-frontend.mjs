import assert from 'node:assert/strict';
const base=process.env.FRONTEND_URL||'http://127.0.0.1:3000';
const response=await fetch(base+'/api/auth/login',{method:'POST',headers:{Origin:base,'Content-Type':'application/json'},body:JSON.stringify({username:'donor',password:process.env.POG_DEMO_PASSWORD||'PoG-demo-2026'})});
assert.equal(response.status,200,await response.text());
const cookie=response.headers.get('set-cookie').split(';')[0];
try {
 const state=await fetch(base+'/api/payment/state',{headers:{Cookie:cookie}});
 const body=await state.json();assert.equal(state.status,200,JSON.stringify(body));assert.equal(body.user.id,'donor');
 const ui=await fetch(base+'/api/payment/ui',{headers:{Cookie:cookie}});assert.equal(ui.status,200);assert.match(await ui.text(),/\/api\/payment\/app.js/);
 const asset=await fetch(base+'/api/payment/app.js',{headers:{Cookie:cookie}});assert.equal(asset.status,200);assert.match(await asset.text(),/const base/);
 const denied=await fetch(base+'/api/payment/operations/exchange',{method:'POST',headers:{Cookie:cookie,Origin:'http://evil.test','Content-Type':'application/json','Idempotency-Key':'frontend-denied'},body:'{"amount":"60"}'});assert.equal(denied.status,403);
 console.log('前端会话 → /api/payment/* → Payment 4010 验证通过（无资金操作）。');
} finally {await fetch(base+'/api/auth/logout',{method:'POST',headers:{Cookie:cookie,Origin:base,'Content-Type':'application/json'},body:'{}'});}
