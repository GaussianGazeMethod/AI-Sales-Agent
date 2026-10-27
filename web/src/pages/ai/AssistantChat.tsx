import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, Drawer, Empty, Input, Select, Space, Tag } from 'antd';
import { Button as MobileButton, TextArea, Toast } from 'antd-mobile';
import { useQuery } from '@tanstack/react-query';
import { assistant, type AssistantResult, type AssistantTurn } from '@/api/assistant';
import { listLeads } from '@/api/leads';
import { useAuthStore } from '@/store/auth';
import PageHeader from '@/components/PageHeader';
import { AiDisclaimer } from '@/components/Guide';
const STAGE_LABEL: Record<string, string> = { s1_valid_opportunity: '有效商机', s2_customer_reached: '客户触达', s3_proposal_quote: '方案报价', s4_budget_confirmed: '经费确认', s5_project_reviewed: '项目过会', s6_bidding: '招标采购', s7_contract_signed: '合同签约' };
import './AssistantChat.css';
import FactList from '@/components/ProjectFacts';

function Receipt({ result }: { result: AssistantResult }) {
  return <div className="assistant-receipt"><p>{result.reply}</p>
    {result.record && <details><summary>本次结构化记录</summary><FactList facts={result.record} /></details>}
    {result.stage && <div className="assistant-stage">当前阶段：{STAGE_LABEL[result.stage.after] ?? result.stage.after}{result.stage.gaps.length > 0 && <p>推进缺口：{result.stage.gaps.join('；')}</p>}</div>}
    {!!result.actions?.length && <div className="assistant-actions"><strong>已承接行动</strong>{result.actions.map(a => <p key={a.id}>{a.content}<small>{a.status === 'done' ? '已做' : a.due_at ? a.due_at.replace('T', ' ') : '时间待确认'}{a.effect ? ` · 效果：${a.effect}` : ''}</small></p>)}</div>}
    {result.report && <details open={!result.record}><summary>{result.report.requirements_status === 'draft' ? '查看需求报告草稿' : '查看报告'}</summary>{result.report.requirements_status === 'draft' && <p role="status">需求报告草稿：{result.report.requirement_gaps?.join('；')}。尚不作为阶段达成依据。</p>}<div className="assistant-report">{result.report.sections.map(s => <section key={s.title}><h4>{s.title}</h4><p>{s.content}</p></section>)}</div><Space wrap><Button size="small" onClick={() => assistant.word(result.report!.id)}>下载 Word</Button>{result.report.suggested_actions.map((a, i) => <Button size="small" key={i} onClick={async () => { await assistant.adopt(result.report!.id, i); Toast.show('已加入待办'); }}>采纳：{a.content}</Button>)}</Space></details>}
    {result.dossier && <FactList facts={result.dossier.facts} />}
  </div>;
}
export default function AssistantChat({ mobile = false }: { mobile?: boolean }) {
  const navigate = useNavigate(); const [params, setParams] = useSearchParams();
  const user = useAuthStore(s => s.user); const token = useAuthStore(s => s.token);
  const [conversation, setConversation] = useState<string>(); const [leadId, setLeadId] = useState<string | undefined>(params.get('lead') || undefined);
  const [turns, setTurns] = useState<AssistantTurn[]>([]); const [text, setText] = useState(''); const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(false); const [drawer, setDrawer] = useState(false); const bottom = useRef<HTMLDivElement>(null);
  const storageKey = `assistant-conversation:${user?.id ?? ''}`;
  const leads = useQuery({ queryKey: ['assistant-leads', user?.id], queryFn: () => listLeads({ view: 'mine', page_size: 100 }), enabled: !!token });
  const conversations = useQuery({ queryKey: ['assistant-conversations', user?.id], queryFn: assistant.list, enabled: !!token });
  const dossier = useQuery({ queryKey: ['assistant-dossier', leadId, turns.length, drawer], queryFn: () => assistant.dossier(leadId!, drawer), enabled: !!leadId });
  useEffect(() => { if (!token) navigate(`/login?next=${encodeURIComponent(mobile ? '/m/ai/chat' : '/ai/chat')}`, { replace: true }); }, [token, navigate, mobile]);
  async function open(id: string) {
    setLoading(true);
    try { const data = await assistant.read(id); setConversation(id); setTurns(data.turns ?? []); setLeadId(data.lead_id || undefined); localStorage.setItem(storageKey, id); }
    finally { setLoading(false); }
  }
  useEffect(() => { const saved = params.get('conversation') || (!params.get('lead') && localStorage.getItem(storageKey)); if (saved && token) void open(saved).catch(() => localStorage.removeItem(storageKey)); }, [storageKey, token]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const requestedLead = params.get('lead');
    if (!requestedLead || conversation || busy || !conversations.data) return;
    const existing = conversations.data.find(c => c.lead_id === requestedLead);
    if (existing) void open(existing.id);
  }, [params, conversation, busy, conversations.data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [turns.length, busy]);
  function fresh() { if (busy) return; setConversation(undefined); setTurns([]); setLeadId(undefined); localStorage.removeItem(storageKey); setParams({}); }
  async function send(retry?: AssistantTurn) {
    const content = retry?.content || text.trim(); if (!content || busy) return; setBusy(true);
    const key = retry?.request_key || crypto.randomUUID();
    let id = conversation;
    try {
      if (!id) { const c = await assistant.create(leadId); id = c.id; setConversation(id); localStorage.setItem(storageKey, id); }
      if (!retry) { setText(''); setTurns(t => [...t, { id: key, request_key: key, content, requested_lead_id: leadId, status: 'processing' }]); }
      const result = await assistant.send(id, key, content, retry ? retry.requested_lead_id : leadId);
      setTurns(t => t.map(item => item.request_key === key ? { ...item, id: result.turn_id, status: 'routed', result } : item));
      if (result.task.lead_id) setLeadId(result.task.lead_id);
      void conversations.refetch(); void leads.refetch();
    } catch { setTurns(t => t.map(item => item.request_key === key ? { ...item, status: 'failed', error: item.result?.overall_status === 'partial' ? '剩余操作未完成，已有记录保持不变。' : '本次未完成，可重试原消息。' } : item)); }
    finally { setBusy(false); }
  }
  const options = (leads.data?.items ?? []).map(l => ({ value: l.id, label: [l.name, l.project_name].filter(Boolean).join(' · ') }));
  return <div className={`assistant-shell ${mobile ? 'assistant-mobile' : ''}`}>
    {!mobile && <aside className="assistant-sidebar"><h2>AI 销售助手</h2><p>记录事实，推动下一步</p><Button type="primary" block onClick={fresh} disabled={busy}>开始新对话</Button><h4>最近对话</h4>{conversations.data?.map(c => <button className={c.id === conversation ? 'active' : ''} key={c.id} disabled={busy} onClick={() => void open(c.id)}>{options.find(o => o.value === (c.id === conversation ? leadId : c.lead_id))?.label || '线索收集'}<small>{c.id === conversation ? '当前对话' : '继续记录'}</small></button>)}<a href="/m/ai/chat" target="_blank" rel="noreferrer">打开手机页面 ↗</a></aside>}
    <main className="assistant-main"><header>{mobile ? <div><h2>销售助手</h2><p>像发消息一样记录，缺少的信息我来问。</p></div> : <PageHeader title="今天，项目有什么新进展？" subtitle="像发消息一样记录，缺少的信息我来问。" /> }<Space><Button disabled={busy} onClick={fresh}>新对话</Button>{leadId && <Button onClick={() => setDrawer(true)}>项目档案</Button>}</Space></header>
      <div className="assistant-context"><Select showSearch allowClear disabled={busy} placeholder="选择已有客户，或直接说新线索" value={leadId} options={options} optionFilterProp="label" onChange={value => setLeadId(value)} style={{ width: '100%' }} />{mobile && !!conversations.data?.length && <Select placeholder="恢复最近对话" value={conversation} onChange={id => void open(id)} options={conversations.data.map(c => ({ value: c.id, label: options.find(o => o.value === c.lead_id)?.label || '线索收集' }))} style={{ width: '100%' }} />}</div>
      <div className="assistant-messages" aria-live="polite">{!turns.length && <div className="assistant-welcome"><span>从一句话开始</span><h1>把客户进展告诉我</h1><p>刚拜访了谁？客户说了什么？<br />你负责沟通，我帮你整理记录与下一步。</p><button onClick={() => setText('渠道商介绍了一位学校联系人，帮我录入线索')}>录入一条新线索 ↗</button><button onClick={() => setText('今天拜访了客户，想记录项目进展')}>记录今天的跟进 ↗</button></div>}{loading && <p>正在恢复对话…</p>}{turns.map(t => <div key={t.request_key} className="assistant-turn"><div className="assistant-user">{t.content}</div>{t.result ? <><Receipt result={t.result} />{t.result.overall_status === 'partial' && <div className="assistant-error">{t.error || '已有记录已保存，剩余操作尚未完成。'}<Button size="small" onClick={() => void send(t)} disabled={busy}>重试未完成步骤</Button></div>}</> : t.status === 'failed' ? <div className="assistant-error">{t.error}<Button size="small" onClick={() => void send(t)} disabled={busy}>重试原消息</Button></div> : null}</div>)}{busy && <p className="assistant-thinking">正在整理事实并核对项目…</p>}<div ref={bottom} /></div>
      <footer><div className="assistant-compose">{mobile ? <TextArea value={text} onChange={setText} placeholder="说说客户反馈或下一步…" autoSize={{ minRows: 1, maxRows: 5 }} maxLength={12000} /> : <Input.TextArea value={text} onChange={e => setText(e.target.value)} placeholder="输入客户反馈、项目进展或需要的分析…" autoSize={{ minRows: 2, maxRows: 6 }} maxLength={12000} onPressEnter={e => { if (!e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />}{mobile ? <MobileButton color="primary" loading={busy} disabled={!text.trim()} onClick={() => void send()}>发送</MobileButton> : <Button type="primary" loading={busy} disabled={!text.trim()} onClick={() => void send()}>发送</Button>}</div><small>保存结果以助手回执为准。模拟讨论请明确说明。<AiDisclaimer text="分析建议供销售判断" /></small></footer>
    </main><Drawer title={`${dossier.data?.customer || '项目'}档案`} open={drawer} onClose={() => setDrawer(false)} width={mobile ? '100%' : 520}>{dossier.data ? <><Tag>修订 {dossier.data.revision ?? '尚未建立'}</Tag><FactList facts={dossier.data.facts} /><h3>事实历史</h3>{dossier.data.history?.map(e => <Card key={e.id} size="small" style={{ marginBottom: 10 }}><small>{e.occurred_at.replace('T', ' ')} · 记录于 {e.recorded_at.replace('T', ' ')}</small><p>{e.content}</p></Card>)}</> : <Empty description="尚无档案记录" />}</Drawer>
  </div>;
}
