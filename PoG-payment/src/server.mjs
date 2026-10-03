import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, scryptSync, timingSafeEqual, createHash } from 'node:crypto';
import { Store, seed, check, Fault } from './core.mjs';
import { Anvil } from './chain.mjs';
import { Payment } from './service.mjs';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const equal = (a, b) => typeof a === 'string' && typeof b === 'string' &&
  timingSafeEqual(createHash('sha256').update(a).digest(), createHash('sha256').update(b).digest());
export function createServer(payment, { secret, password = 'PoG-demo-2026' } = {}) {
  check(secret && secret.length >= 32, '请先执行 npm run setup 生成代理密钥');
  const sessions = new Map(), attempts = new Map();
  const salt = randomBytes(16), passwordHash = scryptSync(password, salt, 32);
  const read = async req => {
    check(req.headers['content-type']?.startsWith('application/json'), '需要 application/json', 415);
    let size = 0; const chunks = [];
    for await (const chunk of req) { size += chunk.length; check(size <= 16384, '请求过大', 413); chunks.push(chunk); }
    try { const value = JSON.parse(Buffer.concat(chunks).toString()); check(value && typeof value === 'object' && !Array.isArray(value), '需要 JSON 对象', 400); return value; }
    catch (e) { throw new Fault(400, '无效的 JSON'); }
  };
  return http.createServer(async (req, res) => {
    const send = (status, data, extra = {}) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', ...extra }); res.end(JSON.stringify(data));
    };
    try {
      const url = new URL(req.url, 'http://localhost');
      if (req.method === 'GET' && ['/', '/app.js', '/style.css'].includes(url.pathname)) {
        const filename = { '/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css' }[url.pathname];
        const contentType = filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html';
        res.writeHead(200, { 'Content-Type': contentType + '; charset=utf-8', 'Cache-Control': 'no-store',
          'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'", 'X-Content-Type-Options': 'nosniff' });
        res.end(fs.readFileSync(path.join(root, 'public', filename))); return;
      }
      if (req.method === 'GET' && url.pathname === '/health') { send(200, { ok: true, mode: payment.db.mode, version: 1 }); return; }
      const proxy = equal(req.headers['x-payment-secret'], secret);
      if (req.method === 'POST' && !proxy) {
        const origin = req.headers.origin;
        check(origin && ['http:', 'https:'].includes(new URL(origin).protocol) && new URL(origin).host === req.headers.host,
          '请求来源不匹配', 403);
      }
      if (req.method === 'POST' && url.pathname === '/api/login') {
        const body = await read(req), remote = req.socket.remoteAddress;
        const entry = attempts.get(remote) || { count: 0, expires: Date.now() + 60000 };
        if (entry.expires < Date.now()) { entry.count = 0; entry.expires = Date.now() + 60000; }
        check(entry.count < 10, '请一分钟后重试', 429);
        const valid = typeof body.password === 'string' && body.password.length < 201 &&
          timingSafeEqual(scryptSync(body.password, salt, 32), passwordHash) && Object.hasOwn(payment.db.accounts, body.username);
        if (!valid) { entry.count++; attempts.set(remote, entry); throw new Fault(401, '账号或密码错误'); }
        attempts.delete(remote);
        for (const [key, session] of sessions) if (session.expires < Date.now()) sessions.delete(key);
        const token = randomBytes(32).toString('hex'); sessions.set(token, { userId: body.username, expires: Date.now() + 8 * 3600000 });
        send(200, { ok: true }, { 'Set-Cookie': `pog-payment=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800` }); return;
      }
      const token = /(?:^|;\s*)pog-payment=([a-f0-9]+)/.exec(req.headers.cookie || '')?.[1];
      const session = sessions.get(token);
      const userId = proxy ? req.headers['x-payment-user'] : session?.expires > Date.now() ? session.userId : null;
      check(typeof userId === 'string', '请先登录', 401); payment.user(userId);
      if (req.method === 'POST' && url.pathname === '/api/logout') {
        sessions.delete(token); send(200, { ok: true }, { 'Set-Cookie': 'pog-payment=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' }); return;
      }
      if (req.method === 'GET' && url.pathname === '/api/state') { send(200, await payment.view(userId)); return; }
      if (req.method === 'POST' && url.pathname.startsWith('/api/operations/')) {
        const body = await read(req);
        const op = await payment.command(userId, url.pathname.slice('/api/operations/'.length), body, req.headers['idempotency-key']);
        send(['pending', 'needs_reconciliation'].includes(op.status) ? 202 : 200, op); return;
      }
      if (req.method === 'POST' && /^\/api\/reconcile\/[^/]+$/.test(url.pathname)) {
        const body = await read(req);
        const op = await payment.reconcile(userId, url.pathname.split('/').at(-1), body.txHash);
        send(200, op); return;
      }
      throw new Fault(404, '接口不存在');
    } catch (error) {
      if (!(error instanceof Fault)) console.error(error);
      if (!res.headersSent) send(error instanceof Fault ? error.status : 500, { error: error instanceof Fault ? error.message : 'Payment 服务异常，请保留幂等键重试' });
    }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.env.PAYMENT_MODE || 'demo';
  check(['demo', 'anvil'].includes(mode), 'PAYMENT_MODE 只能为 demo 或 anvil');
  const chain = mode === 'anvil' ? new Anvil(path.resolve(process.env.PAYMENT_MANIFEST || '../contracts/deployments/local/manifest.json')) : null;
  if (chain) await chain.guard();
  const store = new Store(path.resolve(process.env.PAYMENT_DATA_DIR || '.data/' + mode), seed(mode, chain?.instance || 'demo-v1'));
  const payment = new Payment(store, chain);
  const server = createServer(payment, { secret: process.env.PAYMENT_PROXY_SECRET, password: process.env.PAYMENT_DEMO_PASSWORD });
  const host = process.env.PAYMENT_HOST || '127.0.0.1';
  check(['127.0.0.1', '::1'].includes(host), 'Payment 服务只绑定本机；三电脑请通过前端代理访问');
  const port = Number(process.env.PAYMENT_PORT || 4010);
  server.on('error', error => { store.close(); console.error(error); process.exitCode = 1; });
  server.listen(port, host, () => console.log(`PoG-payment ${mode}: http://${host}:${port}`));
  const stop = () => server.close(() => { store.close(); process.exit(0); });
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
}
