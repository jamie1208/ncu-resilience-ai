import cytoscape, { type Core, type StylesheetJson } from 'cytoscape'
import { useEffect, useRef, useState } from 'react'

import { Badge, Button, Card, Input, Select } from '@design'

import {
  decideRelation,
  listRelations,
  type RelationDecision,
  type RelationEdge,
  type RelationGraph as Graph,
  type RelationNode,
  type RelationType,
} from '../api'
import {
  formatTime,
  label,
  RELATION_DECISION_LABELS,
  RELATION_DECISION_TONES,
  RELATION_METHOD_LABELS,
  RELATION_TYPE_LABELS,
} from '../format'
import { Link } from '../Link'
import { useErrorMessage, useLoad } from '../useLoad'
import { ErrorText, LoadState, Muted, Notice, PageHeader, Section } from '../ui'

const TYPES = Object.keys(RELATION_TYPE_LABELS) as RelationType[]
const DECISIONS = Object.keys(RELATION_DECISION_LABELS) as RelationDecision[]

const DONE: Record<RelationDecision, string> = {
  confirmed: '已確認這條關聯',
  rejected: '已駁回這條關聯',
  pending: '已回滾到待確認',
}

/** 推測原因有方向（原因 → 結果），其餘關聯無方向。 */
function arrow(type: RelationType): string {
  return type === 'hypothesized_cause' ? '→' : '↔'
}

function formatScore(score: number | null): string {
  return score === null ? '—' : score.toFixed(2)
}

/**
 * 事件關聯圖（P6-T02，規格 F07）：AI 與規則找出的關聯（P6-T01）以 Cytoscape.js 呈現，
 * 由使用者確認、駁回或回滾。圖只用來看結構；操作一律走清單與詳情（canvas 無法用鍵盤與報讀器操作）。
 * 推測原因只在後台，不經公開端（ADR-0015）。
 */
export function RelationGraph() {
  const [type, setType] = useState<RelationType | ''>('')
  const [decision, setDecision] = useState<RelationDecision | ''>('')
  const [withRejected, setWithRejected] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const graph = useLoad(
    () => listRelations({ type, decision, include_rejected: withRejected }),
    `relations:${type}:${decision}:${withRejected}`,
  )
  const nodes = graph.data?.nodes ?? []
  const edges = graph.data?.edges ?? []
  const titles = new Map(nodes.map((n) => [n.id, n.title]))
  const selected = edges.find((e) => e.id === selectedId) ?? null

  function replace(updated: RelationEdge) {
    graph.setData((g) => g && { ...g, edges: g.edges.map((e) => (e.id === updated.id ? updated : e)) })
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="事件關聯"
        description="AI 與規則找出的事件關聯，經你確認才算數；推測原因只在後台，不會公開。"
        actions={
          <div className="flex flex-wrap items-center gap-3 text-sm font-medium text-foreground">
            <label className="flex items-center gap-2">
              <span className="shrink-0">類型</span>
              <Select value={type} onChange={(e) => setType(e.target.value as RelationType | '')}>
                <option value="">全部</option>
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {RELATION_TYPE_LABELS[t]}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2">
              <span className="shrink-0">決策</span>
              <Select value={decision} onChange={(e) => setDecision(e.target.value as RelationDecision | '')}>
                <option value="">全部</option>
                {DECISIONS.map((d) => (
                  <option key={d} value={d}>
                    {RELATION_DECISION_LABELS[d]}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex h-11 items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 accent-accent"
                checked={withRejected}
                onChange={(e) => setWithRejected(e.target.checked)}
              />
              <span>包含已駁回</span>
            </label>
          </div>
        }
      />
      <LoadState
        loading={graph.loading && !graph.data}
        error={graph.error}
        empty={edges.length === 0}
        emptyText="目前沒有符合條件的關聯"
      />
      {edges.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <Section id="relation-graph" title="關聯圖">
            <Card className="space-y-2 p-0">
              <GraphCanvas graph={{ nodes, edges }} selectedId={selectedId} onSelect={setSelectedId} />
              <p className="px-4 pb-3 text-xs text-muted-foreground">
                粗線：同一事件；細線：相關事件；虛線箭頭：推測原因（原因 → 結果）。顏色：橘＝待確認、綠＝已確認、紅＝已駁回。點選線條可查看詳情。
              </p>
            </Card>
          </Section>
          <div className="min-w-0 space-y-4">
            {selected && (
              <RelationDetail key={selected.id} edge={selected} nodes={nodes} onChange={replace} />
            )}
            <Section id="relation-list" title="關聯清單">
              <ul className="space-y-2">
                {edges.map((edge) => (
                  <li key={edge.id}>
                    <button
                      type="button"
                      aria-pressed={edge.id === selectedId}
                      onClick={() => setSelectedId(edge.id)}
                      className={
                        'w-full min-h-11 rounded-lg border px-3 py-2 text-left text-sm ' +
                        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
                        (edge.id === selectedId
                          ? 'border-accent bg-card'
                          : 'border-border bg-card hover:bg-muted')
                      }
                    >
                      <span className="block break-words text-foreground">
                        {`${titles.get(edge.source) ?? edge.source} ${arrow(edge.type)} ${titles.get(edge.target) ?? edge.target}`}
                      </span>
                      <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge tone={edge.type === 'hypothesized_cause' ? 'warning' : 'neutral'}>
                          {label(RELATION_TYPE_LABELS, edge.type)}
                        </Badge>
                        <Badge tone={RELATION_DECISION_TONES[edge.decision]}>
                          {label(RELATION_DECISION_LABELS, edge.decision)}
                        </Badge>
                        <span className="font-mono">{formatScore(edge.score)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Section>
          </div>
        </div>
      )}
    </div>
  )
}

/** 設計 token（CSS custom property）的實際值；canvas 無法直接用 Tailwind class。 */
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || 'gray'
}

function graphStyle(): StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        'background-color': token('--color-accent'),
        label: 'data(label)',
        color: token('--color-foreground'),
        // Cytoscape 的 font-family 只接受雙引號
        'font-family': token('--font-sans').replaceAll("'", '"'),
        'font-size': 11,
        'text-wrap': 'wrap',
        'text-max-width': '120px',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        width: 18,
        height: 18,
      },
    },
    {
      selector: 'edge',
      style: { width: 2, 'curve-style': 'bezier', 'line-color': token('--color-muted-foreground') },
    },
    { selector: 'edge[type = "same_event"]', style: { width: 4 } },
    {
      selector: 'edge[type = "hypothesized_cause"]',
      style: { 'line-style': 'dashed', 'target-arrow-shape': 'triangle' },
    },
    { selector: 'edge[decision = "pending"]', style: { 'line-color': token('--color-warning') } },
    { selector: 'edge[decision = "confirmed"]', style: { 'line-color': token('--color-success') } },
    { selector: 'edge[decision = "rejected"]', style: { 'line-color': token('--color-danger'), opacity: 0.4 } },
    { selector: 'edge', style: { 'target-arrow-color': 'data(color)' } },
    {
      selector: 'edge.chosen',
      style: {
        'underlay-color': token('--color-accent'),
        'underlay-opacity': 0.25,
        'underlay-padding': 4,
      },
    },
  ]
}

function edgeColor(decision: RelationDecision): string {
  return token(
    decision === 'confirmed' ? '--color-success' : decision === 'rejected' ? '--color-danger' : '--color-warning',
  )
}

/** Cytoscape 畫布：節點或邊增減時重建（重新排版），決策改變只更新邊的資料，不打亂位置。 */
function GraphCanvas({ graph, selectedId, onSelect }: {
  graph: Graph
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const select = useRef(onSelect)
  useEffect(() => {
    select.current = onSelect
  }, [onSelect])
  const structure = [...graph.nodes.map((n) => n.id), '|', ...graph.edges.map((e) => e.id)].join(',')

  useEffect(() => {
    const instance = cytoscape({
      container: container.current,
      elements: [
        ...graph.nodes.map((n) => ({ data: { id: n.id, label: n.title } })),
        ...graph.edges.map((e) => ({
          data: {
            id: e.id,
            source: e.source,
            target: e.target,
            type: e.type,
            decision: e.decision,
            color: edgeColor(e.decision),
          },
        })),
      ],
      style: graphStyle(),
      layout: { name: 'cose', animate: false, padding: 24 },
      autounselectify: true,
      boxSelectionEnabled: false,
      minZoom: 0.3,
      maxZoom: 3,
    })
    instance.on('tap', 'edge', (e) => select.current(e.target.id()))
    cy.current = instance
    return () => {
      instance.destroy()
      cy.current = null
    }
    // 只在結構改變時重建；決策更新由下一個 effect 處理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structure])

  useEffect(() => {
    const instance = cy.current
    if (!instance) return
    for (const e of graph.edges) {
      instance.getElementById(e.id).data({ decision: e.decision, color: edgeColor(e.decision) })
    }
    instance.edges().removeClass('chosen')
    if (selectedId) instance.getElementById(selectedId).addClass('chosen')
  }, [graph.edges, selectedId, structure])

  return (
    <div
      ref={container}
      role="img"
      aria-label={`事件關聯圖：${graph.nodes.length} 個事件、${graph.edges.length} 條關聯；請用關聯清單操作`}
      className="h-80 w-full sm:h-[28rem]"
    />
  )
}

/** 一條關聯的詳情與決策：待確認可確認或駁回；已決定的只能回滾到待確認（不能直接翻轉）。 */
function RelationDetail({ edge, nodes, onChange }: {
  edge: RelationEdge
  nodes: RelationNode[]
  onChange: (edge: RelationEdge) => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const message = useErrorMessage()
  const source = nodes.find((n) => n.id === edge.source)
  const target = nodes.find((n) => n.id === edge.target)

  async function decide(next: RelationDecision) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await decideRelation(edge.id, next, next === 'pending' ? '' : note)
      onChange(result.edge)
      setNote('')
      setNotice(DONE[next])
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section id="relation-detail" title="關聯詳情">
      <Card className="space-y-3">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <EventLink node={source} id={edge.source} />
          <span aria-hidden className="text-muted-foreground">
            {arrow(edge.type)}
          </span>
          <EventLink node={target} id={edge.target} />
        </p>
        {edge.type === 'hypothesized_cause' && (
          <p className="rounded-lg border border-warning/20 bg-warning-muted px-3 py-2 text-sm text-warning">
            推測，尚未查證：前者可能是後者的原因。只在後台顯示，不會公開。
          </p>
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted-foreground">類型</dt>
          <dd>{label(RELATION_TYPE_LABELS, edge.type)}</dd>
          <dt className="text-muted-foreground">方法</dt>
          <dd>{label(RELATION_METHOD_LABELS, edge.method)}</dd>
          <dt className="text-muted-foreground">分數</dt>
          <dd className="font-mono">{formatScore(edge.score)}</dd>
          <dt className="text-muted-foreground">證據</dt>
          <dd>
            {edge.evidence_ids.length === 0 ? (
              '—'
            ) : (
              <ul className="flex flex-wrap gap-x-2 font-mono text-xs">
                {edge.evidence_ids.map((e) => (
                  <li key={e} className="break-all">
                    {e}
                  </li>
                ))}
              </ul>
            )}
          </dd>
          <dt className="text-muted-foreground">建立</dt>
          <dd className="font-mono">{formatTime(edge.created_at)}</dd>
          <dt className="text-muted-foreground">決策</dt>
          <dd className="space-y-1">
            <Badge tone={RELATION_DECISION_TONES[edge.decision]}>
              {label(RELATION_DECISION_LABELS, edge.decision)}
            </Badge>
            {edge.decision !== 'pending' && (
              <span className="block text-xs text-muted-foreground">
                {`${edge.decided_by ?? '—'}・${formatTime(edge.decided_at)}`}
                {edge.decision_note && `・備註：${edge.decision_note}`}
              </span>
            )}
          </dd>
        </dl>
        {notice && <Notice>{notice}</Notice>}
        {error && <ErrorText>{error}</ErrorText>}
        {edge.decision === 'pending' ? (
          <form aria-label="記錄關聯判斷" className="space-y-2" onSubmit={(e) => e.preventDefault()}>
            <label className="block space-y-1 text-sm text-foreground">
              <span>備註（選填）</span>
              <Input
                value={note}
                maxLength={500}
                placeholder="會寫入稽核紀錄，請勿填寫個資"
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => void decide('confirmed')}>
                確認
              </Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void decide('rejected')}>
                駁回
              </Button>
            </div>
          </form>
        ) : (
          <div className="space-y-2">
            <Muted>已決定的關聯要先回滾到待確認，才能改判。</Muted>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void decide('pending')}>
              回滾到待確認
            </Button>
          </div>
        )}
      </Card>
    </Section>
  )
}

function EventLink({ node, id }: { node: RelationNode | undefined; id: string }) {
  return (
    <Link
      to={`/admin/events/${encodeURIComponent(id)}`}
      className="text-accent underline underline-offset-4 break-words"
    >
      {node?.title ?? id}
    </Link>
  )
}
