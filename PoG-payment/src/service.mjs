import { amount, atomic, check, digest, id, textField } from './core.mjs';
import fs from 'node:fs';

const pending = op => !['completed', 'failed'].includes(op.status);
const at = () => new Date().toISOString();
const plus = (a, b) => (BigInt(a) + BigInt(b)).toString();
const minus = (a, b) => (BigInt(a) - BigInt(b)).toString();
const objectId = v => textField(v, '标识');

export class Payment {
  constructor(store, chain = null) { this.store = store; this.chain = chain; }
  get db() { return this.store.db; }
  user(userId) { const u = Object.hasOwn(this.db.accounts, userId) ? this.db.accounts[userId] : null; check(u, '未配置的 Payment 用户', 403); return u; }
  role(u, ...roles) { check(roles.includes(u.role), '此身份不能执行该操作', 403); }
  project(projectId) { const p = this.db.projects.find(p => p.id === projectId); check(p, '项目不存在', 404); return p; }
  owned(u, p) { check(u.id === p.foundationId, '不属于该基金会的项目', 403); }
  log(u, action, reference) { this.db.audit.push({ id: id(), actorId: u.id, action, reference, at: at() }); }
  async refresh(p) { if (this.chain) Object.assign(p, await this.chain.project(p.chainProjectId)); }
  async balance(u) { return this.chain ? this.chain.balance(u.id) : u.tokens; }
  async view(userId) {
    return this.store.run(async () => {
      const u = this.user(userId);
      if (this.chain) await this.chain.guard();
      for (const p of this.db.projects) {
        await this.refresh(p);
        if (this.chain && u.role === 'donor') {
          const e = await this.chain.entitlement(p.chainProjectId, u.id);
          p.entitlements[u.id] = e.amount; p.claimed[u.id] = e.claimed; p.donors[u.id] = e.credit;
        }
      }
      const holdCents = this.db.operations.filter(o => o.userId === u.id && o.kind === 'exchange' && pending(o)).reduce((n, o) => n + o.cents, 0);
      return {
        mode: this.db.mode, instance: this.db.instance, user: { ...u, tokens: await this.balance(u), holdCents, availableHkdCents: u.hkdCents - holdCents,
          wallet: this.chain?.wallet(u.id) || null },
        notice: this.chain ? '本地链测试币真实到账；HKD 为模拟账本，无银行资金。' : '纯演示：HKD 和测试币均在本地账本模拟，未连接区块链。',
        projects: this.db.projects.map(p => ({ ...p, donors: undefined, entitlements: undefined, claimed: undefined,
          myDonation: p.donors[u.id] || '0', myRefund: p.entitlements[u.id] || '0', myClaimed: !!p.claimed[u.id] })),
        releases: u.role === 'donor' ? [] : this.db.releases.filter(r => u.role === 'admin' || this.project(r.projectId).foundationId === u.id),
        operations: this.db.operations.filter(o => u.role === 'admin' || o.userId === u.id),
        ledger: this.db.ledger.filter(l => u.role === 'admin' || l.userId === u.id),
        audit: u.role === 'admin' ? this.db.audit : [],
      };
    });
  }
  async command(userId, kind, body, key) {
    return this.store.run(async () => {
      try { return await this.execute(userId, kind, body, key); }
      catch (error) {
        // Discard uncommitted in-memory mutations; submission checkpoints survive.
        this.store.db = JSON.parse(fs.readFileSync(this.store.file, 'utf8'));
        throw error;
      }
    });
  }
  async execute(userId, kind, body, key) {
    const u = this.user(userId);
    check(typeof key === 'string' && /^[A-Za-z0-9_-]{8,100}$/.test(key), '需要8–100位 Idempotency-Key', 400);
    check(body && typeof body === 'object' && !Array.isArray(body), '需要 JSON 对象', 400);
    const fingerprint = digest([kind, Object.fromEntries(Object.entries(body).sort(([a], [b]) => a.localeCompare(b)))]);
    const old = this.db.operations.find(o => o.userId === u.id && o.key === key);
    if (old) { check(old.fingerprint === fingerprint, '同一个幂等键不能用于不同请求'); return old; }
    if (this.chain) await this.chain.guard();
    // A pending/ambiguous RPC blocks new writes, including a different idempotency
    // key. Reconcile the existing operation rather than minting/transferring twice.
    check(!this.db.operations.some(o => pending(o)), '有待确认操作，请先核对原操作再继续');
    const op = { id: id(), userId: u.id, kind, key, fingerprint, createdAt: at(), status: 'prepared', steps: [],
      projectId: body.projectId, releaseId: body.releaseId, cents: 0, units: '0' };
    let p, release;
    if (body.projectId) { p = this.project(objectId(body.projectId)); await this.refresh(p); }
    if (body.releaseId) {
      release = this.db.releases.find(r => r.id === body.releaseId);
      check(release, '拨款记录不存在', 404);
      p = this.project(release.projectId); op.projectId = p.id;
      await this.refresh(p);
      if (this.chain) {
        const current = await this.chain.release(release.txHash, release.logIndex, p);
        check(current.blockHash === release.blockHash, '原拨款事件已失效');
        release.returned = current.chainReturned;
      }
    }
    if (['exchange', 'donate', 'redeem', 'cashout', 'return', 'demo-release'].includes(kind)) {
      op.cents = amount(body.amount); op.units = atomic(op.cents);
    }
    const finish = () => {
      op.status = 'completed'; op.completedAt = at(); this.db.operations.push(op);
      this.log(u, kind, op.id); this.store.save(); return op;
    };
    if (kind === 'register-project') {
      this.role(u, 'admin'); check(this.chain, '演示模式已有两个独立测试项目');
      const projectId = objectId(body.id);
      const chainProjectId = objectId(body.chainProjectId).toLowerCase();
      check(!this.db.projects.some(x => x.id === projectId || x.chainProjectId === chainProjectId), '项目已登记');
      const state = await this.chain.project(chainProjectId);
      this.db.projects.push({ id: projectId, chainProjectId, name: textField(body.name, '项目名称'), foundationId: 'foundation', paymentPaused: false,
        donors: {}, entitlements: {}, claimed: {}, ...state });
      return finish();
    }
    if (kind === 'pause' || kind === 'resume') {
      this.role(u, 'foundation', 'admin'); check(p, '请选择项目', 400);
      if (u.role === 'foundation') this.owned(u, p);
      check(p.state === 'Active', '只有活跃项目可暂停或恢复 Payment 入口');
      op.reason = textField(body.reason, '原因'); p.paymentPaused = kind === 'pause';
      return finish();
    }
    if (kind === 'import-release') {
      this.role(u, 'foundation'); check(p && this.chain, '需要本地链项目'); this.owned(u, p);
      const r = await this.chain.release(body.txHash, body.logIndex, p);
      check(!this.db.releases.some(x => x.eventId === r.eventId || x.procurementId === r.procurementId), '此拨款已经关联');
      check(r.chainState >= 9 && r.chainState <= 12, '采购尚未放款');
      const entry = { ...r, id: id(), projectId: p.id, redeemed: '0', returned: r.chainReturned };
      this.db.releases.push(entry); op.releaseId = entry.id; op.units = r.units;
      return finish();
    }
    if (kind === 'demo-release') {
      this.role(u, 'admin'); check(!this.chain && p, '仅用于纯演示模式的人工批准拨款');
      check(p.state === 'Active' && !p.paymentPaused, '项目已暂停或正在关闭');
      const procurementId = textField(body.procurementId, '采购凭证编号');
      check(!this.db.releases.some(r => r.procurementId === procurementId), '此采购已拨款');
      check(BigInt(op.units) <= BigInt(p.deposits) - BigInt(p.released) + BigInt(p.returned) - BigInt(p.reserved), '项目余额不足');
      p.released = plus(p.released, op.units); p.unresolved += 1;
      const foundation = this.user(p.foundationId); foundation.tokens = plus(foundation.tokens, op.units);
      const entry = { id: id(), projectId: p.id, procurementId, units: op.units, redeemed: '0', returned: '0', txHash: null, eventId: null, externalReconciled: false };
      this.db.releases.push(entry); op.releaseId = entry.id;
      return finish();
    }
    if (kind === 'demo-reconcile') {
      this.role(u, 'admin'); check(!this.chain && release, '仅用于演示外部核账结果');
      check(!release.externalReconciled && release.returned === '0', '已核账或已退回拨款，不能按全额结算核账');
      check(release.redeemed === release.units, '模拟兑付未完成');
      release.externalReference = textField(body.reference, 'Foundation 在外部处理付款后的核账凭证');
      release.externalReconciled = true; p.unresolved -= 1;
      return finish();
    }
    if (kind === 'exchange') {
      this.role(u, 'donor'); check(u.hkdCents >= op.cents, '模拟 HKD 余额不足');
    } else if (kind === 'cashout') {
      this.role(u, 'donor'); check(BigInt(await this.balance(u)) >= BigInt(op.units), '测试币余额不足');
    } else if (kind === 'donate') {
      this.role(u, 'donor'); check(p, '请选择项目', 400);
      check(p.state === 'Active' && !p.paymentPaused && !p.registryPaused, '项目已暂停或不再接受捐款');
      check(BigInt(await this.balance(u)) >= BigInt(op.units), '测试币余额不足，请先兑换');
      check(p.donors[u.id] !== undefined || Object.keys(p.donors).length < 64, '项目捐款人数已达上限');
    } else if (kind === 'redeem' || kind === 'return') {
      this.role(u, 'foundation'); check(release, '请选择已关联拨款', 400); this.owned(u, p);
      check(BigInt(op.units) <= BigInt(release.units) - BigInt(release.returned) - BigInt(release.redeemed), '金额超过此拨款尚未兑付/退回的余额');
      check(BigInt(await this.balance(u)) >= BigInt(op.units), 'Foundation 测试币余额不足');
      if (kind === 'redeem') check(!p.paymentPaused && !p.registryPaused && p.state === 'Active', '暂停或关闭中的项目不可发起新兑付');
      else check(p.state === 'Closing', '必须先申请关闭项目，才能退回未兑付拨款');
    } else if (kind === 'closing') {
      this.role(u, 'foundation'); check(p, '请选择项目', 400); this.owned(u, p);
      check(p.state === 'Active', '项目已经申请关闭'); op.reason = textField(body.reason, '关闭原因');
    } else if (kind === 'close') {
      this.role(u, 'admin'); check(p && p.state === 'Closing', 'Foundation 尚未申请关闭');
      check(p.unresolved === 0 && p.reserved === '0', '仍有未结采购或预算预留，不能生成退款快照');
    } else if (kind === 'claim') {
      this.role(u, 'donor'); check(p && p.state === 'Refundable', '项目尚未完成关闭核账');
      if (this.chain) {
        const e = await this.chain.entitlement(p.chainProjectId, u.id);
        p.entitlements[u.id] = e.amount; p.claimed[u.id] = e.claimed; p.donors[u.id] = e.credit;
      }
      check(!p.claimed[u.id], '已经领取退款'); check(BigInt(p.donors[u.id] || '0') > 0n, '只有原捐款人能领取');
      op.units = p.entitlements[u.id];
    } else check(false, '接口不存在', 404);

    if (this.chain) op.steps = await this.chain.plan(kind, userId, p, op.units, release);
    this.db.operations.push(op); this.log(u, kind + ':prepared', op.id); this.store.save();
    return this.drive(op);
  }
  async drive(op) {
    if (!pending(op)) return op;
    if (this.chain) {
      try {
        await this.chain.guard();
        for (const step of op.steps) {
          if (step.status === 'confirmed') {
            check(await this.chain.verifyStep(step) === 'confirmed', '之前确认的交易已失效，需人工核账');
            continue;
          }
          if (step.status === 'prepared') {
            step.nonce = await this.chain.rpc('eth_getTransactionCount', [step.from, 'pending']);
            // Preflight reverts can safely fail without broadcasting.
            try { step.gas = await this.chain.rpc('eth_estimateGas', [{ from: step.from, to: step.to, data: step.data, value: '0x0' }]); }
            catch (e) { op.status = 'failed'; op.error = e.message; this.store.save(); return op; }
            step.status = 'submitting'; op.status = 'pending'; this.store.save();
            step.txHash = await this.chain.rpc('eth_sendTransaction', [{ from: step.from, to: step.to, data: step.data, nonce: step.nonce, gas: step.gas, value: '0x0' }]);
            step.status = 'submitted'; this.store.save();
          }
          if (!step.txHash) { op.status = 'needs_reconciliation'; this.store.save(); return op; }
          const status = await this.chain.verifyStep(step);
          if (status === 'pending') { op.status = 'pending'; this.store.save(); return op; }
          if (status === 'failed') { op.status = 'failed'; op.error = '链上交易回滚，未记入 HKD 账本'; this.store.save(); return op; }
          step.status = 'confirmed'; this.store.save();
        }
      } catch (e) { op.status = 'needs_reconciliation'; op.error = e.message; this.store.save(); return op; }
    }
    this.apply(op); return op;
  }
  apply(op) {
    const u = this.user(op.userId), p = op.projectId ? this.project(op.projectId) : null;
    const release = this.db.releases.find(r => r.id === op.releaseId);
    const simulation = !this.chain;
    const n = op.units;
    let hkdDelta = 0;
    if (op.kind === 'exchange') { hkdDelta = -op.cents; if (simulation) u.tokens = plus(u.tokens, n); }
    if (op.kind === 'cashout' || op.kind === 'redeem') {
      hkdDelta = op.cents; if (simulation) u.tokens = minus(u.tokens, n);
      if (release) release.redeemed = plus(release.redeemed, n);
    }
    if (op.kind === 'return') {
      release.returned = plus(release.returned, n);
      if (simulation) { p.returned = plus(p.returned, n); u.tokens = minus(u.tokens, n); }
    }
    if (op.kind === 'donate' && simulation) {
      u.tokens = minus(u.tokens, n); p.deposits = plus(p.deposits, n); p.donors[u.id] = plus(p.donors[u.id] || '0', n);
    }
    if (op.kind === 'closing' && simulation) p.state = 'Closing';
    if (op.kind === 'close' && simulation) {
      const pool = BigInt(p.deposits) - BigInt(p.released) + BigInt(p.returned) - BigInt(p.refunded);
      const deposits = BigInt(p.deposits);
      let cumulative = 0n;
      for (const [donor, credit] of Object.entries(p.donors)) {
        const before = cumulative; cumulative += BigInt(credit);
        p.entitlements[donor] = (pool * cumulative / deposits - pool * before / deposits).toString();
      }
      p.refundPool = pool.toString(); p.state = pool === 0n ? 'Closed' : 'Refundable';
    }
    if (op.kind === 'claim') {
      p.claimed[u.id] = true;
      if (simulation) {
        u.tokens = plus(u.tokens, n); p.refunded = plus(p.refunded, n);
        if (Object.keys(p.donors).every(donor => p.claimed[donor])) p.state = 'Closed';
      }
    }
    u.hkdCents += hkdDelta;
    check(Number.isSafeInteger(u.hkdCents) && u.hkdCents >= 0, '账本余额不合法');
    const entry = { id: id(), operationId: op.id, userId: u.id, kind: op.kind, projectId: op.projectId, releaseId: op.releaseId,
      hkdDeltaCents: hkdDelta, tokenAtomic: n, hkdBalanceCents: u.hkdCents, at: at(), mode: this.db.mode,
      txHashes: op.steps.map(s => s.txHash).filter(Boolean), note: '模拟HKD；不是供应商付款凭证' };
    entry.receiptHash = digest(entry); this.db.ledger.push(entry);
    op.status = 'completed'; op.completedAt = at(); delete op.error;
    this.log(u, op.kind + ':completed', op.id); this.store.save();
  }
  async reconcile(userId, operationId, txHash) {
    return this.store.run(async () => {
      const u = this.user(userId), op = this.db.operations.find(o => o.id === operationId);
      check(op && (op.userId === userId || u.role === 'admin'), '找不到可访问的操作', 404);
      if (!pending(op)) return op;
      check(this.chain, '演示操作无需链上核对'); await this.chain.guard();
      if (txHash) {
        this.role(u, 'admin'); check(/^0x[0-9a-fA-F]{64}$/.test(txHash), '无效的交易Hash', 400);
        check(!this.db.operations.some(o => o.steps.some(s => s.txHash?.toLowerCase() === txHash.toLowerCase())), '交易已被其他步骤使用');
        const step = op.steps.find(s => s.status === 'submitting' && !s.txHash);
        check(step, '当前没有丢失交易Hash的步骤');
        const candidate = { ...step, txHash };
        // Invalid recovery hashes must NEVER replace the persisted intent.
        check(await this.chain.verifyStep(candidate) !== 'pending', '交易尚未确认');
        Object.assign(step, candidate, { status: 'submitted' }); this.store.save();
      }
      return this.drive(op);
    });
  }
}
