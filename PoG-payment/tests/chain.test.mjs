import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Anvil, word } from '../src/chain.mjs';
const address=n=>'0x'+n.repeat(40),hash=n=>'0x'+n.repeat(64);
const setup=t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'pog-chain-test-'));
 const file=path.join(dir,'manifest.json');
 fs.writeFileSync(file,JSON.stringify({runId:'run-1',chain:{rpcUrl:'http://127.0.0.1:8545',chainId:31337,instanceId:'instance-1'},
 roles:{donorA:address('1'),donorB:address('2'),foundation:address('3'),mockRedemption:address('4'),deployerOwner:address('5')},
 contracts:{MockHKD:{address:address('6')},PoGRegistryV2:{address:address('7')},ProcurementEscrowV2:{address:address('8')}}}));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return new Anvil(file);
};
test('receipt validation requires canonical block, exact sender, nonce, input and event',async t=>{
 const chain=setup(t),step={from:address('1'),to:address('6'),nonce:'0x5',data:'0x1234',txHash:hash('a'),events:[{address:address('6'),topics:[hash('e')],data:'0x'+word(60000000)}]};
 const tx={from:step.from,to:step.to,nonce:'0x5',input:step.data,value:'0x0'};
 const receipt={status:'0x1',blockNumber:'0x10',blockHash:hash('b'),logs:[{...step.events[0]}]};
 let blockHash=receipt.blockHash;
 chain.rpc=async method=>method==='eth_getTransactionReceipt'?receipt:method==='eth_getBlockByNumber'?{hash:blockHash}:tx;
 assert.equal(await chain.verifyStep(step),'confirmed');
 tx.nonce='0x4';await assert.rejects(chain.verifyStep(step),/不匹配/);tx.nonce='0x5';
 tx.from=address('2');await assert.rejects(chain.verifyStep(step),/不匹配/);tx.from=step.from;
 receipt.logs=[];await assert.rejects(chain.verifyStep(step),/预期事件/);
 receipt.status='0x0';assert.equal(await chain.verifyStep(step),'failed');
 blockHash=hash('c');await assert.rejects(chain.verifyStep(step),/规范链/);
});
test('Foundation release import checks contract, project, recipient and invoice',async t=>{
 const chain=setup(t),projectId=hash('1'),procurementId=hash('2'),topic=hash('3');
 const log={address:chain.escrow,logIndex:'0x2',topics:[topic,procurementId,projectId,'0x'+word(chain.roles.foundation)],data:'0x'+word(72000000)+word(8000000)};
 chain.signature=async()=>topic;
 const transfer=await chain.transferEvent(chain.escrow,chain.roles.foundation,'72000000');
 chain.receipt=async()=>({status:'0x1',blockHash:hash('a'),logs:[log,{...transfer,logIndex:'0x1'}]});
 const proc=Array(22).fill(word(0));proc[1]=projectId.slice(2);proc[11]=word(72000000);proc[21]=word(9);
 chain.call=async()=>proc;
 const release=await chain.release(hash('4'),2,{chainProjectId:projectId});assert.equal(release.units,'72000000');assert.equal(release.procurementId,procurementId);
 proc[11]=word(71000000);await assert.rejects(chain.release(hash('4'),2,{chainProjectId:projectId}),/不匹配/);proc[11]=word(72000000);
 log.address=chain.token;await assert.rejects(chain.release(hash('4'),2,{chainProjectId:projectId}),/不是该项目/);
});
test('chain instance mismatch fails closed before any writes',async t=>{
 const chain=setup(t);chain.rpc=async method=>method==='eth_chainId'?'0x7a69':{instanceId:'new-instance'};
 await assert.rejects(chain.guard(),/已重置/);
});
