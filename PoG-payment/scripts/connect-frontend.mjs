import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const target = path.resolve(process.argv[2] || '..');
if (!fs.existsSync(path.join(target, 'src/lib/auth.ts')) || !fs.existsSync(path.join(target, 'src/lib/store.ts'))) throw new Error('目标不是当前 PoG 前端');
const env = fs.readFileSync(path.join(root, '.env'), 'utf8');
const secret = /^PAYMENT_PROXY_SECRET=(.+)$/m.exec(env)?.[1];
const port = /^PAYMENT_PORT=(\d+)$/m.exec(env)?.[1] || '4010';
if (!secret || secret.length < 32) throw new Error('请先 npm run setup');
const copies = [['frontend/route.ts.txt','src/app/api/payment/[...path]/route.ts'],['frontend/page.tsx.txt','src/app/payment/page.tsx']];
for (const [source, destination] of copies) {
  const file = path.join(target, destination), content = fs.readFileSync(path.join(root, source), 'utf8');
  if (fs.existsSync(file) && fs.readFileSync(file, 'utf8') !== content) throw new Error(`已有不同文件，未覆盖：${file}`);
}
const envPath = path.join(target, '.env.local');
let original = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8') : '';
for (const [key, value] of [['PAYMENT_PROXY_SECRET',secret],['PAYMENT_API_URL',`http://127.0.0.1:${port}`]]) {
  const present = new RegExp(`^${key}=(.*)$`,'m').exec(original);
  if (present && present[1] !== value) throw new Error(`${key} 已配置不同值，请先核对，未覆盖`);
  if (!present) original += `\n${key}=${value}\n`;
}
for (const [source,destination] of copies) {const file=path.join(target,destination);fs.mkdirSync(path.dirname(file),{recursive:true});fs.copyFileSync(path.join(root,source),file);}
fs.writeFileSync(envPath,original,{mode:0o600});
const portal = path.join(target,'src/components/portal-app.tsx');
if(fs.existsSync(portal)) {
  let source=fs.readFileSync(portal,'utf8');
  if(!source.includes('href="/payment"') && source.includes('<div className="sidebar-bottom">')) {
    source=source.replace('<div className="sidebar-bottom">', '<div className="sidebar-bottom">\n          {["donor", "foundation", "admin"].includes(app.user.role) && <a href="/payment" className="nav-item">Payment · 模拟兑换与退款</a>}');
    fs.writeFileSync(portal,source);
  }
}
console.log(`已连接 ${target}。重启前端后登录并打开 /payment；浏览器通过 /api/payment/* 调用本机 ${port} 服务。`);
