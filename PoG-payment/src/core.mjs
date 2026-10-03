import { randomUUID, createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export class Fault extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const check = (ok, message, status = 409) => { if (!ok) throw new Fault(status, message); };
export const id = () => randomUUID();
export const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const amount = value => {
  check(typeof value === 'string' && /^(0|[1-9]\d{0,8})(\.\d{1,2})?$/.test(value), '金额须为最多两位小数的字符串', 400);
  const [whole, fraction = ''] = value.split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  check(cents > 0, '金额必须大于零', 400);
  return cents;
};
export const atomic = cents => (BigInt(cents) * 10000n).toString();
export const centsOf = units => {
  check(BigInt(units) % 10000n === 0n, '此笔金额含不足一分的测试币，请保留在钱包，不可舍入兑付');
  const n = Number(BigInt(units) / 10000n);
  check(Number.isSafeInteger(n), '金额超出账本范围');
  return n;
};
export const textField = (v, label) => {
  check(typeof v === 'string' && v.trim().length > 0 && v.length <= 200, `缺少或无效的${label}`, 400);
  return v.trim();
};
export function seed(mode, instance) {
  return { version: 1, mode, instance, accounts: {
    donor: { id: 'donor', role: 'donor', name: 'Donor A', orgId: 'personal-a', hkdCents: 100000, tokens: '0' },
    donor2: { id: 'donor2', role: 'donor', name: 'Donor B', orgId: 'personal-b', hkdCents: 100000, tokens: '0' },
    foundation: { id: 'foundation', role: 'foundation', name: 'Foundation', orgId: 'org-foundation', hkdCents: 0, tokens: '0' },
    admin: { id: 'admin', role: 'admin', name: '管理员', orgId: 'platform', hkdCents: 0, tokens: '0' },
  }, projects: mode === 'demo' ? ['P-001', 'P-REFUND'].map(projectId => ({
    id: projectId, name: projectId === 'P-001' ? '模拟采购项目' : '全额退款项目', foundationId: 'foundation',
    paymentPaused: false, state: 'Active', deposits: '0', released: '0', returned: '0', refunded: '0', reserved: '0',
    unresolved: 0, donors: {}, entitlements: {}, claimed: {},
  })) : [], releases: [], operations: [], ledger: [], audit: [] };
}

// One writer per directory. Atomic replacement + fsync makes the submission intent
// durable BEFORE an RPC write. A crashed process may be restarted without resending.
export class Store {
  constructor(directory, initial) {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    this.file = path.join(directory, 'ledger.json');
    this.lock = path.join(directory, 'writer.lock');
    try { this.fd = fs.openSync(this.lock, 'wx', 0o600); }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      const pid = Number(fs.readFileSync(this.lock, 'utf8'));
      check(Number.isInteger(pid) && pid > 0, 'writer.lock 无效，请人工核对后移除');
      let alive = true;
      try { process.kill(pid, 0); } catch (err) { if (err.code === 'ESRCH') alive = false; }
      check(!alive, '该账本已被另一个 Payment 进程使用');
      fs.unlinkSync(this.lock);
      this.fd = fs.openSync(this.lock, 'wx', 0o600);
    }
    fs.writeFileSync(this.fd, String(process.pid));
    try {
      this.db = fs.existsSync(this.file) ? JSON.parse(fs.readFileSync(this.file, 'utf8')) : initial;
      check(this.db.version === 1 && this.db.mode === initial.mode && this.db.instance === initial.instance,
        '账本模式或链实例不匹配。请使用新的数据目录，不可复用旧轮次账本');
      this.save();
    } catch (e) { this.close(); throw e; }
    this.tail = Promise.resolve();
  }
  save() {
    const temp = this.file + '.tmp';
    const fd = fs.openSync(temp, 'w', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(this.db, null, 2)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    fs.renameSync(temp, this.file);
    const dir = fs.openSync(path.dirname(this.file), 'r');
    try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
  }
  run(fn) {
    const result = this.tail.then(async () => {
      try { return await fn(); }
      catch (error) {
        // Also covers reconciliation: a failed final save must not leave a
        // partially-applied balance in memory that could be applied again.
        this.db = JSON.parse(fs.readFileSync(this.file, 'utf8'));
        throw error;
      }
    });
    this.tail = result.catch(() => {});
    return result;
  }
  close() { if (this.fd !== undefined) { fs.closeSync(this.fd); fs.unlinkSync(this.lock); this.fd = undefined; } }
}
