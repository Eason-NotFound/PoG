import fs from 'node:fs';
import { randomBytes } from 'node:crypto';
if (!fs.existsSync('.env')) {
  const sample = fs.readFileSync('.env.example', 'utf8');
  fs.writeFileSync('.env', sample.replace('PAYMENT_PROXY_SECRET=', 'PAYMENT_PROXY_SECRET=' + randomBytes(32).toString('hex')), { mode: 0o600, flag: 'wx' });
  console.log('已生成 .env。运行 npm start；默认使用纯演示模式。');
} else console.log('.env 已存在，保留现有配置。');
