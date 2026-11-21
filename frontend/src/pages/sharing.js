import{store}from'../stores/business.js';
import{esc,num}from'../utils/format.js';
import{table}from'../components/common/table.js';
import{notice}from'../components/common/notice.js';
import{SHARE_STATUS,GROUPS,EPSILON_PRESETS}from'../constants/enums.js';
import{getBudget,getShares,previewDp,publicDownloadUrl}from'../api/shares.js';

export function sharingPage(state){
 const reports=store.reports.filter(r=>r.status==='completed');
 const editable=store.user.role!=='viewer';
 return `<header class=page-head><div><span class=eyebrow>05 / PRIVACY-SAFE SHARING</span><h1>对外共享 · 差分隐私均分</h1><p>对外只发布 Laplace 加噪均分与公开分组人数。相邻数据集定义为替换一名学员成绩，整次发布满足 ε-差分隐私。</p></div></header>
 <div class=split>
 <section>
 <div class=section-title><h2>隐私预算余额</h2><span>按方案累计 · 跨报告共用</span></div>
 <div id=budget-panel>${budgetPanel(state)}</div>
 <div class=section-title style="margin-top:28px"><h2>扣费记录</h2><span>与共享结果同时生效，只增不改</span></div>
 <div id=ledger-panel>${ledgerPanel(state)}</div>
 <div class=section-title style="margin-top:28px"><h2>共享申请</h2><span>${state.shares.length} 条</span></div>
 <div id=share-list>${shareTable(state)}</div>
 </section>
 <aside class=side-sheet>
 <span class=eyebrow>NEW SHARING REQUEST</span><h2>发起对外共享</h2>
 ${editable?shareForm(state,reports):notice('观察员为只读账号，可查看预算与下载已生成的对外内容，但不能发起共享。')}
 ${notice('对外内容不含个人明细、原始均分或原始检验结论；内部报告页的原始统计照常使用，不受共享影响。')}
 <div id=dp-preview class=dp-preview><span class=eyebrow>噪声误差试算（不扣费）</span><p class=muted>选择报告并填写隐私预算后自动展示。</p></div>
 </aside></div>`;
}

export function budgetPanel(state){
 if(!state.budgetAll)return '<div class=empty>加载中…</div>';
 const rows=state.budgetAll.budgets;
 if(!rows.length)return notice('还没有方案预算。发起首份共享时自动创建。');
 return table(['方案','累计上限 ε','已花费 ε','剩余 ε'],rows.map(b=>{
  const pct=Math.min(100,b.cap_epsilon?b.spent_epsilon/b.cap_epsilon*100:0);
  return [esc(b.plan_title||('方案 '+b.plan_id)),num(b.cap_epsilon,3),num(b.spent_epsilon,3),
   `<b>${num(b.remaining_epsilon,3)}</b><div class=budget-bar><span style="width:${pct.toFixed(1)}%"></span></div>`];
 }));
}

export function ledgerPanel(state){
 if(!state.budgetAll)return '<div class=empty>加载中…</div>';
 const rows=state.budgetAll.ledger;
 if(!rows.length)return notice('尚无扣费。共享生成成功时与结果在同一事务内扣费。');
 return table(['时间 UTC','方案','申请 №','本次 ε','累计花费'],rows.map(l=>[
  esc(l.created_at.slice(0,19).replace('T',' ')),String(l.plan_id),String(l.share_request_id),num(l.epsilon,3),num(l.spent_after,3)]));
}

export function shareTable(state){
 if(!state.shares.length)return notice('还没有共享申请。在右侧选择已完成报告并设置隐私预算。');
 return table(['申请 № / 编号','报告','ε','状态','噪声误差与下载'],state.shares.map(s=>{
  const badge=`<span class="badge ${s.status==='ready'?'completed':s.status==='failed'?'failed':'running'}">${SHARE_STATUS[s.status]||s.status}</span>`;
  let detail=badge;
  if(s.status==='ready'&&s.public_payload){
   const errs=s.public_payload.groups.map(g=>`${GROUPS[g.group]||g.group}：发布值 ${num(g.private_mean,2)}，95% 误差 ±${num(g.error_budget.prob_95pct_abs_error_within,2)}`).join('<br>');
   detail=`${badge}<small>${errs}</small><br><a class=text-btn href="${publicDownloadUrl(s.download_token)}" target=_blank rel=noopener>对外链接（免登录）↓</a> <button class=text-btn data-share-download=${s.id}>登录态下载</button>`;
  }else if(s.status==='failed'){
   detail=`${badge}<small class=muted>${esc(s.error||'生成失败，可重试')}</small><br><button class=text-btn data-share-retry=${s.id}>重试恢复</button>`;
  }else{
   detail=`${badge}<small class=muted>生成中或等待重试，中断后可恢复且不重复扣费</small><br><button class=text-btn data-share-retry=${s.id}>重新入队</button>`;
  }
  return [`<b>№ ${s.id}</b><small>${esc(s.idempotency_key)}</small>`,String(s.report_id),num(s.epsilon,3),detail];
 }));
}

function shareForm(state,reports){
 if(!reports.length)return notice('没有已完成的报告，无法从样本快照聚合均分。');
 return `<form id=share-form>
 <label>来源报告（取其不可变样本快照）<select name=report_id>${reports.map(r=>`<option value=${r.id}>№ ${r.id} · ${esc(store.plans.find(p=>p.id===r.plan_id)?.title||'方案 '+r.plan_id)}</option>`).join('')}</select></label>
 <label>隐私预算 ε（越大噪声越小，扣 ε）<input name=epsilon type=number min=0.05 max=10 step=0.05 value=0.5 required></label>
 <div class=epsilon-row>${EPSILON_PRESETS.map(v=>`<button type=button class=quiet data-epsilon=${v}>ε = ${v}</button>`).join('')}</div>
 <label>申请编号（幂等键，同编号重试不重复扣费）<input name=idempotency_key minlength=8 maxlength=80 required placeholder="share-2026-0001" value="share-${new Date().toISOString().slice(0,10)}-${Math.random().toString(36).slice(2,8)}"></label>
 <label class=check><input name=fail_injected type=checkbox> 演示：首次生成注入中断（验证可恢复）</label>
 <button class=primary>提交共享申请 →</button>
 <button type=button class=quiet id=refresh-sharing>刷新状态</button>
 </form>`;
}

export async function renderPreview(container,reportId,epsilon){
 const eps=parseFloat(epsilon);
 if(!(eps>=0.05&&eps<=10)){container.innerHTML='<span class=eyebrow>噪声误差试算（不扣费）</span><p class=muted>ε 需在 0.05–10 之间。</p>';return;}
 try{
  const data=await previewDp(reportId,eps);
  container.innerHTML=`<span class=eyebrow>噪声误差试算（不扣费）</span>${data.groups.map(g=>`<div class=dp-preview-group><b>${GROUPS[g.group]||g.group}</b><small>公开人数 n = ${g.n}　替换一人敏感性 Δ = ${num(g.sensitivity,4)}</small><small>Laplace 尺度 b = Δ/ε = ${num(g.scale_b,4)}</small><small>平均绝对误差 MAE ≈ ${num(g.mae,3)}　RMSE ≈ ${num(g.rmse,3)}</small><small>95% 概率 |误差| ≤ ${num(g.bound_95,3)}　99% ≤ ${num(g.bound_99,3)}</small></div>`).join('')}`;
 }catch(e){container.innerHTML=`<span class=eyebrow>噪声误差试算</span><p class=muted>${esc(e.message)}</p>`;}
}
