const root = document.getElementById('app');
const embedded = location.pathname.startsWith('/api/payment/');
const base = embedded ? '/api/payment' : '/api';
let state, busy = false;
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = cents => (cents / 100).toFixed(2);
const coin = units => { const n = BigInt(units || '0'); return `${n / 1000000n}.${(n % 1000000n).toString().padStart(6, '0')}`; };
const kinds = {exchange:'Donor 兑换',donate:'捐入项目',redeem:'Foundation 兑付',cashout:'测试币换回 HKD',claim:'领取退款',closing:'申请关闭',close:'关闭核账',return:'退回未兑付拨款',pause:'暂停 Payment 入口',resume:'恢复 Payment 入口','demo-release':'演示批准拨款','import-release':'关联链上拨款','demo-reconcile':'登记外部核账','register-project':'关联链上项目'};
async function api(path, body, key) {
  const r = await fetch(base + path, {credentials:'same-origin', cache:'no-store', method:body ? 'POST':'GET', headers:body ? {'Content-Type':'application/json', ...(key ? {'Idempotency-Key':key}: {})}: {}, body:body ? JSON.stringify(body):undefined});
  const data = await r.json();
  if (!r.ok) { const error = new Error(data.error || '请求失败'); error.status = r.status; throw error; }
  return data;
}
function message(value, error = false) { const box = document.getElementById('message'); if (box) {box.textContent = value; box.className = 'message' + (error ? ' error':'');} }
function projectOptions() {return state.projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.state)}${p.paymentPaused?' · Payment 已暂停':''}</option>`).join('');}
function releaseOptions() {return state.releases.map(r => `<option value="${esc(r.id)}">${esc(r.procurementId)} · ${coin(r.units)} mHKD</option>`).join('');}
const field = (name, title, value = '', type = 'text') => `<label>${title}<input name="${name}" type="${type}" value="${esc(value)}" required ${name==='amount'?'inputmode="decimal"':''}></label>`;
const projectSelect = () => `<label>项目<select name="projectId" required>${projectOptions()}</select></label>`;
const releaseSelect = () => `<label>已关联拨款<select name="releaseId" required>${releaseOptions()}</select></label>`;
function form(kind, fields, text = '') {return `<form data-kind="${kind}" class="card"><h2>${esc(kinds[kind])}</h2>${text?`<p class="muted">${text}</p>`:''}${fields}<button>${esc(kinds[kind])}</button></form>`;}
function login() {
  root.innerHTML = `<div class="login card"><span class="tag">PoG / PAYMENT</span><h1>模拟兑换工作台</h1><p>可用账号：donor、donor2、foundation、admin</p><div id="message" class="message"></div><form id="login">${field('username','账号','donor')}${field('password','演示密码','PoG-demo-2026','password')}<button>登录</button></form><small>本地演示账号，无真实资金。</small></div>`;
  document.getElementById('login').onsubmit = async e => {e.preventDefault();try{await api('/login',Object.fromEntries(new FormData(e.target)));await refresh();}catch(err){message(err.message,true);}};
}
async function refresh() {try{state = await api('/state');render();}catch(err){if(err.status===401&&!embedded)login();else{root.innerHTML='<div class="card"><h1>Payment 暂不可用</h1><div id="message"></div><button id="retry">重试连接</button></div>';message(err.message,true);document.getElementById('retry').onclick=refresh;}}}
function render() {
  const u = state.user, donor = u.role === 'donor', foundation = u.role === 'foundation', admin = u.role === 'admin';
  const history = [...state.operations].reverse();
  root.innerHTML = `<header><div><span class="tag">PoG / PAYMENT · ${state.mode==='demo'?'纯演示':'LOCAL ANVIL'}</span><h1>${esc(u.name)} · 模拟兑换</h1></div><div><button class="secondary" id="refresh">刷新</button>${embedded?'':'<button class="secondary" id="logout">退出</button>'}</div></header>
  <p class="notice">${esc(state.notice)} 兑付到账不代表供应商收款。退款先回原 Donor 测试币钱包。</p><div id="message" class="message" role="status" aria-live="polite"></div>
  <div class="grid"><section class="card"><small>模拟 HKD 可用余额</small><b class="balance">HK$ ${money(u.availableHkdCents)}</b><small>待确认冻结：${money(u.holdCents)}</small></section><section class="card"><small>${state.mode==='demo'?'模拟测试币余额':'链上 MockHKD 余额'}</small><b class="balance">${coin(u.tokens)}</b><code>${esc(u.wallet || '未接链，不生成交易 Hash')}</code></section></div>
  <div class="grid">${donor ? form('exchange',field('amount','模拟 HKD → mHKD','60'),'1:1，零手续费。确认到账后扣除模拟 HKD。')+form('donate',projectSelect()+field('amount','捐款金额','60'),'与旧前端“模拟捐款”分开记账，请在此完成测试币捐款。')+form('cashout',field('amount','mHKD → 模拟 HKD','16.80'),'适用于退款或未捐出的测试币。最多两位小数，细碎余额保留在钱包。') : ''}
  ${foundation ? form('redeem',releaseSelect()+field('amount','兑付金额','72'),'核对已拨款金额，将测试币转到模拟兑付钱包，再记 HKD 到账。') + (state.mode==='anvil'?form('import-release',projectSelect()+field('txHash','FundsReleasedToFoundation 交易 Hash')+field('logIndex','事件 logIndex','0','number')):''):''}</div>
  <section class="card"><h2>项目与退款</h2><p class="muted">暂停只拦截本 Payment 服务的新捐款和兑付；链上关闭须完成原有人工审批。退款领取不受本地暂停影响。</p><div class="table"><table><thead><tr><th>项目</th><th>状态</th><th>捐入 / 拨出 / 已退</th>${donor?'<th>我的捐款 / 可退</th>':''}</tr></thead><tbody>${state.projects.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.state)}${p.paymentPaused?' · 已暂停':''}</td><td>${coin(p.deposits)} / ${coin(p.released)} / ${coin(p.refunded)}</td>${donor?`<td>${coin(p.myDonation)} / ${coin(p.myRefund)} ${p.myClaimed?'已领取':''}</td>`:''}</tr>`).join('')}</tbody></table></div></section>
  <div class="grid">${donor?form('claim',projectSelect(),'关闭核账后领取原钱包退款；重复领取会被阻止。'):''}
  ${foundation?form('pause',projectSelect()+field('reason','原因','补充核账材料'))+form('resume',projectSelect()+field('reason','原因','问题已解决'))+form('closing',projectSelect()+field('reason','关闭原因','项目结束，核对剩余资金'))+form('return',releaseSelect()+field('amount','退回尚未兑付的测试币','1'),'仅 Closing 阶段可用。退回不消除未结采购义务；已兑为 HKD 的部分需先另行处理。'):''}
  ${admin?form('close',projectSelect(),state.mode==='anvil'?'执行合约 executeClose；需要审批组事先提交有效关闭批准。':'检查未结采购与预算预留后生成比例退款快照。')+(state.mode==='demo'?form('demo-release',projectSelect()+field('procurementId','唯一采购凭证编号','PO-72')+field('amount','获批金额','72'),'仅构造明确标注的演示拨款。不会生成 txHash。')+form('demo-reconcile',releaseSelect()+field('reference','Foundation 外部处理付款后的凭证编号'),'仅登记演示外部核账结果，不转账、不扣 Foundation HKD。'):form('register-project',field('id','前端项目 ID','P-001')+field('name','项目名称','公益项目')+field('chainProjectId','链上 bytes32 项目 ID'),'关联已由 Foundation 创建的链上项目。')):''}</div>
  ${state.releases.length?`<section class="card"><h2>Foundation 拨款记录</h2><div class="table"><table><thead><tr><th>采购</th><th>获批测试币</th><th>已兑付</th><th>已退回</th><th>凭证</th></tr></thead><tbody>${state.releases.map(r=>`<tr><td>${esc(r.procurementId)}</td><td>${coin(r.units)}</td><td>${coin(r.redeemed)}</td><td>${coin(r.returned)}</td><td>${r.txHash?esc(r.txHash):'模拟拨款（无 txHash）'}</td></tr>`).join('')}</tbody></table></div></section>`:''}
  <section class="card"><h2>操作记录与关联凭证</h2><p class="muted">重复提交会复用同一幂等键。如要主动发起相同金额的新一笔操作，请使用下方按钮。失败或待核对请求应先解决。</p><button class="secondary" id="new-intent">开始新一笔操作</button><button class="secondary" id="download">下载我的账本与凭证</button>${history.length?history.map(o=>`<details><summary>${esc(kinds[o.kind]||o.kind)} · ${esc(o.status)} · ${coin(o.units)} mHKD</summary><p>${esc(o.error||'')}</p><code>${esc(o.id)}</code><pre>${esc(JSON.stringify(o,null,2))}</pre>${!['completed','failed'].includes(o.status)?`<button data-reconcile="${esc(o.id)}">重新查询确认结果</button>${admin?`<label>若广播结果丢失，填入经核查的原始交易 Hash<input id="tx-${esc(o.id)}" placeholder="0x…"></label>`:''}`:''}</details>`).join(''):'<p class="empty">暂无操作</p>'}</section>`;
  document.getElementById('refresh').onclick=refresh;
  if (!embedded) document.getElementById('logout').onclick=async()=>{await api('/logout',{});login();};
  document.getElementById('download').onclick=()=>{const blob=new Blob([JSON.stringify({notice:state.notice,user:u.id,ledger:state.ledger,operations:state.operations},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='pog-payment-receipts.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
  document.getElementById('new-intent').onclick=()=>{
    if(state.operations.some(o=>!['completed','failed'].includes(o.status))){message('请先核对待确认操作。',true);return;}
    for(const k of Object.keys(sessionStorage))if(k.startsWith(`pog-payment:${u.id}:`))sessionStorage.removeItem(k);
    message('已开始新一笔操作。下一次提交将使用新的幂等键。');
  };
  root.querySelectorAll('form[data-kind]').forEach(f=>f.onsubmit=async e=>{
    e.preventDefault();if(busy)return;busy=true;const button=f.querySelector('button');button.disabled=true;
    const body=Object.fromEntries(new FormData(f));if(body.logIndex!==undefined)body.logIndex=Number(body.logIndex);
    const kind=f.dataset.kind, fingerprint=`pog-payment:${u.id}:${kind}:${JSON.stringify(body)}`;
    let key=sessionStorage.getItem(fingerprint);if(!key){key=Array.from(crypto.getRandomValues(new Uint8Array(20)),x=>x.toString(16).padStart(2,'0')).join('');sessionStorage.setItem(fingerprint,key);}
    try{const op=await api('/operations/'+kind,body,key);await refresh();message(`${kinds[kind]}：${op.status}${op.error?' · '+op.error:''}`,op.status==='failed'||op.status==='needs_reconciliation');}
    catch(err){message(err.message,true);}finally{busy=false;button.disabled=false;}
  });
  root.querySelectorAll('[data-reconcile]').forEach(button=>button.onclick=async()=>{if(busy)return;busy=true;button.disabled=true;try{const op=await api('/reconcile/'+button.dataset.reconcile,{txHash:document.getElementById('tx-'+button.dataset.reconcile)?.value||undefined});await refresh();message('核对结果：'+op.status,op.status==='failed');}catch(err){message(err.message,true);}finally{busy=false;button.disabled=false;}});
}
refresh();
