import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Store, seed, amount } from '../src/core.mjs';
import { Payment } from '../src/service.mjs';
const setup = t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pog-payment-'));
  const store = new Store(dir, seed('demo','demo-v1'));
  const service = new Payment(store); let serial=0;
  t.after(()=>{store.close();fs.rmSync(dir,{recursive:true,force:true});});
  const run=(who,kind,body={},key)=>service.command(who,kind,body,key||`test-key-${++serial}`);
  return {store,service,run,dir};
};
const fund = async run => {for(const [user,n] of [['donor','60'],['donor2','40']]){await run(user,'exchange',{amount:n});await run(user,'donate',{projectId:'P-001',amount:n});}};

test('60 exchange: double-click, different payload, persistence and receipts',async t=>{
 const {service,run,dir}=setup(t);
 const results=await Promise.all(Array.from({length:8},()=>run('donor','exchange',{amount:'60'},'same-request-key')));
 assert.equal(new Set(results.map(x=>x.id)).size,1);
 const v=await service.view('donor');assert.equal(v.user.hkdCents,94000);assert.equal(v.user.tokens,'60000000');assert.equal(v.ledger.length,1);
 assert.equal(v.ledger[0].txHashes.length,0);assert.equal(v.ledger[0].receiptHash.length,64);
 await assert.rejects(run('donor','exchange',{amount:'61'},'same-request-key'),/不同请求/);
 assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'ledger.json'))).accounts.donor.hkdCents,94000);
});

test('amount precision, balances and authorization reject without mutation',async t=>{
 const {run,service}=setup(t);
 for(const value of ['0','-1','1.001','1e2','NaN',60,' 60','1000000000'])assert.throws(()=>amount(value));
 await assert.rejects(run('donor','exchange',{amount:'1000.01'}),/余额不足/);
 await assert.rejects(run('foundation','exchange',{amount:'60'}),/身份/);
 await assert.rejects(run('admin','redeem',{amount:'72'}),/身份/);
 await assert.rejects(run('donor','exchange',{amount:'60'},'short'),/Idempotency/);
 assert.equal((await service.view('donor')).ledger.length,0);
});

test('72 release → redemption; no supplier payment; over-redemption blocked',async t=>{
 const {run,service}=setup(t);await fund(run);
 const release=await run('admin','demo-release',{projectId:'P-001',procurementId:'PO-72',amount:'72'});
 assert.equal((await service.view('foundation')).user.hkdCents,0);
 assert.equal((await service.view('foundation')).user.tokens,'72000000');
 await assert.rejects(run('admin','demo-release',{projectId:'P-001',procurementId:'PO-72',amount:'72'}),/已拨款/);
 await run('foundation','redeem',{releaseId:release.releaseId,amount:'72'});
 const v=await service.view('foundation');assert.equal(v.user.hkdCents,7200);assert.equal(v.user.tokens,'0');assert.equal(v.releases[0].redeemed,'72000000');
 await assert.rejects(run('foundation','redeem',{releaseId:release.releaseId,amount:'72'}),/超过/);
 await assert.rejects(run('foundation','pay-vendor',{}),/接口不存在/);
 assert.equal(v.projects[0].unresolved,1);
});

test('remaining refund 100 - 72: A receives 16.8, B 11.2, cashout optional',async t=>{
 const {run,service}=setup(t);await fund(run);
 const r=await run('admin','demo-release',{projectId:'P-001',procurementId:'PO-72',amount:'72'});
 await run('foundation','redeem',{releaseId:r.releaseId,amount:'72'});
 await run('foundation','closing',{projectId:'P-001',reason:'完成'});
 await assert.rejects(run('admin','close',{projectId:'P-001'}),/未结/);
 await run('admin','demo-reconcile',{releaseId:r.releaseId,reference:'EXTERNAL-72'});
 await run('admin','close',{projectId:'P-001'});
 assert.equal((await service.view('donor')).projects[0].myRefund,'16800000');
 await run('donor','claim',{projectId:'P-001'});
 await assert.rejects(run('donor','claim',{projectId:'P-001'}),/已经领取/);
 await run('donor2','claim',{projectId:'P-001'});
 assert.equal((await service.view('donor2')).user.tokens,'11200000');
 assert.equal((await service.view('donor')).projects[0].state,'Closed');
 await run('donor','cashout',{amount:'16.80'});
 assert.equal((await service.view('donor')).user.hkdCents,95680);
 assert.equal((await service.view('foundation')).user.hkdCents,7200,'No supplier debit in Payment');
});

test('pause is not refund; recovery closes and refunds 60/40 completely',async t=>{
 const {run,service}=setup(t);await fund(run);
 await run('foundation','pause',{projectId:'P-001',reason:'异常'});
 await assert.rejects(run('donor','donate',{projectId:'P-001',amount:'1'}),/暂停/);
 await assert.rejects(run('donor','claim',{projectId:'P-001'}),/核账/);
 await run('foundation','resume',{projectId:'P-001',reason:'已查明'});
 await run('foundation','pause',{projectId:'P-001',reason:'终止'});
 await run('foundation','closing',{projectId:'P-001',reason:'取消项目'});
 await run('admin','close',{projectId:'P-001'});
 await run('donor','claim',{projectId:'P-001'});await run('donor2','claim',{projectId:'P-001'});
 assert.equal((await service.view('donor')).user.tokens,'60000000');
 assert.equal((await service.view('donor2')).user.tokens,'40000000');
});

test('partial release return restores custody but keeps unresolved debt blocked',async t=>{
 const {run,service}=setup(t);await fund(run);
 const r=await run('admin','demo-release',{projectId:'P-001',procurementId:'PO-72',amount:'72'});
 await assert.rejects(run('foundation','return',{releaseId:r.releaseId,amount:'72'}),/先申请关闭/);
 await run('foundation','closing',{projectId:'P-001',reason:'异常'});
 await run('foundation','return',{releaseId:r.releaseId,amount:'72'});
 await assert.rejects(run('admin','close',{projectId:'P-001'}),/未结/);
 await assert.rejects(run('admin','demo-reconcile',{releaseId:r.releaseId,reference:'x'}),/退回/);
 assert.equal((await service.view('foundation')).projects[0].returned,'72000000');
});

test('donors see only their own operations and receipts',async t=>{
 const {run,service}=setup(t);await fund(run);
 const v=await service.view('donor2');assert(v.operations.every(o=>o.userId==='donor2'));assert(v.ledger.every(o=>o.userId==='donor2'));
 assert.equal(v.projects[0].donors,undefined);assert.equal(v.releases.length,0);
});

test('rounding intervals conserve the complete snapshot at atomic precision',async t=>{
 const {store,run,service}=setup(t);
 const p=store.db.projects[0];p.state='Closing';p.deposits='3';p.released='1';p.donors={donor:'1',donor2:'2'};store.save();
 await run('admin','close',{projectId:'P-001'});
 assert.equal((await service.view('donor')).projects[0].myRefund,'0');
 await run('donor','claim',{projectId:'P-001'});await run('donor2','claim',{projectId:'P-001'});
 assert.equal(store.db.projects[0].refunded,'2');assert.equal(store.db.projects[0].state,'Closed');
});

test('zero-funded project closes directly and a second writer is rejected',async t=>{
 const {run,service,dir}=setup(t);
 assert.throws(()=>new Store(dir,seed('demo','demo-v1')),/另一个/);
 await run('foundation','closing',{projectId:'P-001',reason:'取消'});
 await run('admin','close',{projectId:'P-001'});
 assert.equal((await service.view('donor')).projects[0].state,'Closed');
});

test('ambiguous RPC keeps HKD on hold; recovery never broadcasts twice',async t=>{
 const {store}=setup(t);let sends=0;
 const chain={guard:async()=>{},balance:async()=> '0',wallet:()=> '0xabc',project:async()=>({}),
 entitlement:async()=>({amount:'0',credit:'0',claimed:false}),
 plan:async()=>[{from:'0xabc',to:'0xdef',data:'0x01',status:'prepared'}],
 rpc:async(method)=>{if(method==='eth_getTransactionCount')return '0x5';if(method==='eth_estimateGas')return '0x20000';if(method==='eth_sendTransaction'){sends++;throw new Error('timeout after accepted');}},
 verifyStep:async(step)=>{assert.equal(step.nonce,'0x5');return 'confirmed';}};
 const service=new Payment(store,chain);
 const o=await service.command('donor','exchange',{amount:'60'},'ambiguous-key');
 assert.equal(o.status,'needs_reconciliation');assert.equal(store.db.accounts.donor.hkdCents,100000);
 assert.equal((await service.view('donor')).user.holdCents,6000);
 await assert.rejects(service.command('donor','exchange',{amount:'60'},'different-key'),/待确认/);
 await service.reconcile('donor',o.id);assert.equal(sends,1);
 await service.reconcile('admin',o.id,'0x'+'a'.repeat(64));
 assert.equal(store.db.accounts.donor.hkdCents,94000);assert.equal(store.db.ledger.length,1);
 await service.reconcile('admin',o.id);assert.equal(store.db.ledger.length,1);assert.equal(sends,1);
});
