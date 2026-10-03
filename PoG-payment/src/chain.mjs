import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { check } from './core.mjs';

const ZERO = '0x' + '0'.repeat(40);
const lower = x => String(x).toLowerCase();
export const word = x => {
  if (typeof x === 'string' && /^0x[0-9a-fA-F]{40}$|^0x[0-9a-fA-F]{64}$/.test(x)) return x.slice(2).toLowerCase().padStart(64, '0');
  return BigInt(x).toString(16).padStart(64, '0');
};
const words = data => {
  check(/^0x(?:[0-9a-fA-F]{64})*$/.test(data), '无效的链上返回数据', 502);
  return data.slice(2).match(/.{64}/g) || [];
};
const addr = w => '0x' + w.slice(-40);
const uint = w => BigInt('0x' + w).toString();

export class Anvil {
  constructor(manifestPath) {
    this.manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const m = this.manifest;
    const url = new URL(m.chain.rpcUrl);
    check(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname), '只允许本机 Anvil RPC');
    check(m.chain.chainId === 31337 && m.chain.instanceId && m.runId, '需要 M3.1 本地链清单');
    this.url = url.href;
    this.instance = m.runId + ':' + m.chain.instanceId;
    this.token = m.contracts.MockHKD.address;
    this.registry = m.contracts.PoGRegistryV2.address;
    this.escrow = m.contracts.ProcurementEscrowV2.address;
    this.roles = m.roles;
    this.signatures = new Map();
  }
  async rpc(method, params = []) {
    const response = await fetch(this.url, { method: 'POST', signal: AbortSignal.timeout(8000),
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
    check(response.ok, 'RPC 无法连接', 502);
    const body = await response.json();
    check(!body.error, `RPC ${method}: ${body.error?.message || '错误'}`, 502);
    return body.result;
  }
  async signature(s) {
    if (!this.signatures.has(s)) this.signatures.set(s, await this.rpc('web3_sha3', ['0x' + Buffer.from(s).toString('hex')]));
    return this.signatures.get(s);
  }
  async data(s, args = []) { return (await this.signature(s)).slice(0, 10) + args.map(word).join(''); }
  async call(to, signature, args = []) {
    return words(await this.rpc('eth_call', [{ to, data: await this.data(signature, args) }, 'latest']));
  }
  wallet(userId) {
    const role = { donor: 'donorA', donor2: 'donorB', foundation: 'foundation', admin: 'deployerOwner' }[userId];
    check(role && this.roles[role], '此账号尚未配置链上钱包', 403);
    return this.roles[role];
  }
  async guard() {
    const m = this.manifest;
    check(BigInt(await this.rpc('eth_chainId')) === 31337n, '链 ID 不匹配');
    check((await this.rpc('anvil_metadata')).instanceId === m.chain.instanceId, 'Anvil 已重置，请换新清单和账本');
    check((await this.rpc('eth_getBlockByNumber', ['0x0', false])).hash === m.chain.genesisHash, '创世区块不匹配');
    for (const c of Object.values(m.contracts)) {
      const code = await this.rpc('eth_getCode', [c.address, 'latest']);
      check(code !== '0x' && createHash('sha256').update(Buffer.from(code.slice(2), 'hex')).digest('hex') === c.runtime.sha256,
        '合约字节码不匹配');
      const block = await this.rpc('eth_getBlockByNumber', ['0x' + Number(c.deployment.blockNumber).toString(16), false]);
      check(block?.hash === c.deployment.blockHash, '部署区块已失效');
    }
    check(Number(BigInt('0x' + (await this.call(this.token, 'decimals()'))[0])) === 6, 'MockHKD 必须为6位小数');
    check(lower(addr((await this.call(this.registry, 'escrow()'))[0])) === lower(this.escrow) &&
      lower(addr((await this.call(this.escrow, 'registry()'))[0])) === lower(this.registry), 'V2 合约未互相绑定');
    check(new Set(['donorA', 'donorB', 'foundation', 'mockRedemption'].map(k => lower(this.roles[k]))).size === 4,
      'Donor、Foundation 和兑付钱包必须分离');
  }
  async balance(userId) { return uint((await this.call(this.token, 'balanceOf(address)', [this.wallet(userId)]))[0]); }
  async project(chainId) {
    check(/^0x[0-9a-fA-F]{64}$/.test(chainId), 'chainProjectId 必须是 bytes32', 400);
    const p = await this.call(this.registry, 'getProject(bytes32)', [chainId]);
    const l = await this.call(this.escrow, 'getLedger(bytes32)', [chainId]);
    check(lower(addr(p[1])) === lower(this.roles.foundation) && lower(addr(p[3])) === lower(this.token), '项目 Foundation 或资产不匹配');
    return { state: ['Active', 'Closing', 'Refundable', 'Closed'][Number(uint(p[7]))], unresolved: Number(uint(p[8])),
      deposits: uint(l[1]), reserved: uint(l[2]), released: uint(l[3]), returned: uint(l[4]), refunded: uint(l[5]), refundPool: uint(l[6]),
      registryPaused: uint((await this.call(this.registry, 'paused()'))[0]) === '1' };
  }
  async entitlement(chainId, userId) {
    const args = [chainId, this.wallet(userId)];
    return { amount: uint((await this.call(this.escrow, 'refundEntitlement(bytes32,address)', args))[0]),
      credit: uint((await this.call(this.escrow, 'donorCredit(bytes32,address)', args))[0]),
      claimed: uint((await this.call(this.escrow, 'refundClaimed(bytes32,address)', args))[0]) === '1' };
  }
  async event(address, signature, indexed, values) {
    return { address: lower(address), topics: [await this.signature(signature), ...indexed.map(x => '0x' + word(x))], data: '0x' + values.map(word).join('') };
  }
  async transferEvent(from, to, n) { return this.event(this.token, 'Transfer(address,address,uint256)', [from, to], [n]); }
  async step(from, to, signature, args, events = []) { return { from, to, data: await this.data(signature, args), events, status: 'prepared' }; }
  async plan(kind, userId, p, units, release) {
    const from = this.wallet(userId);
    if (kind === 'exchange') return [await this.step(this.roles.deployerOwner, this.token, 'mint(address,uint256)', [from, units], [await this.transferEvent(ZERO, from, units)])];
    if (kind === 'redeem' || kind === 'cashout') return [await this.step(from, this.token, 'transfer(address,uint256)', [this.roles.mockRedemption, units], [await this.transferEvent(from, this.roles.mockRedemption, units)])];
    if (kind === 'donate' || kind === 'return') return [
      await this.step(from, this.token, 'approve(address,uint256)', [this.escrow, units]),
      await this.step(from, this.escrow, kind === 'donate' ? 'deposit(bytes32,uint256)' : 'returnReleasedFunds(bytes32,uint256)',
        [kind === 'donate' ? p.chainProjectId : release.procurementId, units], [await this.transferEvent(from, this.escrow, units)]),
    ];
    if (kind === 'claim') return [await this.step(from, this.escrow, 'claimRefund(bytes32)', [p.chainProjectId],
      [await this.event(this.escrow, 'RefundClaimed(bytes32,address,uint256)', [p.chainProjectId, from], [units])])];
    if (kind === 'closing') return [await this.step(from, this.registry, 'requestClosing(bytes32)', [p.chainProjectId])];
    if (kind === 'close') return [await this.step(from, this.escrow, 'executeClose(bytes32)', [p.chainProjectId])];
    throw new Error('Unsupported chain operation');
  }
  async release(txHash, logIndex, p) {
    check(/^0x[0-9a-fA-F]{64}$/.test(txHash) && Number.isSafeInteger(logIndex) && logIndex >= 0, '请输入真实 txHash 和整数 logIndex', 400);
    const receipt = await this.receipt(txHash);
    check(receipt && receipt.status === '0x1', '拨款交易尚未成功确认');
    const log = receipt.logs.find(l => Number(BigInt(l.logIndex)) === logIndex);
    const topic = await this.signature('FundsReleasedToFoundation(bytes32,bytes32,address,uint256,uint256)');
    check(log && lower(log.address) === lower(this.escrow) && log.topics[0] === topic &&
      lower(log.topics[2]) === lower(p.chainProjectId) && lower(addr(log.topics[3])) === lower(this.roles.foundation), '不是该项目的 Foundation 拨款事件');
    const procurementId = log.topics[1];
    const proc = await this.call(this.registry, 'getProcurement(bytes32)', [procurementId]);
    const units = uint(words(log.data)[0]);
    check(lower(proc[1]) === lower(p.chainProjectId.slice(2)) && uint(proc[11]) === units, '拨款金额或采购记录不匹配');
    const transfer = await this.transferEvent(this.escrow, this.roles.foundation, units);
    check(receipt.logs.some(l => lower(l.address) === transfer.address &&
      JSON.stringify(l.topics.map(lower)) === JSON.stringify(transfer.topics.map(lower)) && lower(l.data) === lower(transfer.data)),
      '拨款回执缺少实际转入 Foundation 的 MockHKD Transfer 事件');
    return { procurementId, units, chainReturned: uint(proc[20]), chainState: Number(uint(proc[21])), txHash, logIndex,
      blockHash: receipt.blockHash, eventId: `${lower(txHash)}:${logIndex}` };
  }
  async receipt(hash) {
    const r = await this.rpc('eth_getTransactionReceipt', [hash]);
    if (!r) return null;
    const block = await this.rpc('eth_getBlockByNumber', [r.blockNumber, false]);
    check(block?.hash === r.blockHash, '交易不在当前规范链上');
    return r;
  }
  async verifyStep(step) {
    const r = await this.receipt(step.txHash);
    if (!r) return 'pending';
    const tx = await this.rpc('eth_getTransactionByHash', [step.txHash]);
    check(tx && lower(tx.from) === lower(step.from) && lower(tx.to) === lower(step.to) && lower(tx.input) === lower(step.data) &&
      BigInt(tx.nonce) === BigInt(step.nonce) && BigInt(tx.value) === 0n, '恢复交易与原始操作不匹配');
    if (r.status !== '0x1') return 'failed';
    for (const expected of step.events) check(r.logs.some(l => lower(l.address) === expected.address &&
      JSON.stringify(l.topics.map(lower)) === JSON.stringify(expected.topics.map(lower)) && lower(l.data) === lower(expected.data)), '交易缺少预期事件，必须人工核账');
    step.blockHash = r.blockHash;
    return 'confirmed';
  }
}
