import { Badge, Card } from '@design'

import { listSkills, type SkillEval, type SkillSummary } from '../api'
import { formatPercent, formatTime, formatUsd } from '../format'
import { useLoad } from '../useLoad'
import { LoadState, Muted, PageHeader } from '../ui'

/** Skills 儀表板：每個產品 Skill 的版本、近 7 天執行（失敗率、費用）與目前版本的評測結果。 */
export function SkillsDashboard() {
  const skills = useLoad(listSkills, 'skills')
  const items = skills.data ?? []
  return (
    <div className="space-y-4">
      <PageHeader title="Skills" description="產品 Skills 的版本、近 7 天執行統計與評測結果。" />
      <LoadState loading={skills.loading} error={skills.error} empty={items.length === 0} emptyText="沒有產品 Skill" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((skill) => (
          <SkillCard key={skill.name} skill={skill} />
        ))}
      </div>
    </div>
  )
}

function Stat({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd className="font-mono text-sm text-foreground">{value}</dd>
    </div>
  )
}

function SkillCard({ skill }: { skill: SkillSummary }) {
  const runs = skill.runs_last_7d
  return (
    <Card as="article" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-sm font-semibold text-foreground">{skill.name}</h2>
        <span className="font-mono text-xs text-muted-foreground">{`v${skill.version}`}</span>
      </div>
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground">近 7 天</h3>
        <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          <Stat term="執行" value={String(runs.runs)} />
          <Stat term="失敗率" value={formatPercent(runs.failure_rate)} />
          <Stat term="abstain" value={String(runs.abstained)} />
          <Stat term="費用" value={formatUsd(runs.cost_usd)} />
        </dl>
        {runs.versions.length > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">{`執行版本：${runs.versions.join('、')}`}</p>
        )}
      </div>
      <EvalSummary result={skill.eval} />
    </Card>
  )
}

function EvalSummary({ result }: { result: SkillEval | null }) {
  if (!result) return <Muted>尚無此版本的評測報告</Muted>
  const source = result.mode === 'live' ? `真實模型 ${result.model ?? ''}`.trim() : '錄製回應'
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={result.passed ? 'success' : 'danger'}>{result.passed ? '評測通過' : '評測未通過'}</Badge>
        <span className="text-xs text-muted-foreground">
          {`${source} · ${result.split ?? '—'} · ${formatTime(result.generated_at)}`}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <Stat term="案例" value={`${result.cases_passed ?? '—'}／${result.cases ?? '—'}`} />
        <Stat term="Schema 通過" value={formatPercent(result.schema_pass_rate)} />
        <Stat term="abstain 率" value={formatPercent(result.abstain_rate)} />
        <Stat term="評測費用" value={formatUsd(result.cost_usd)} />
      </dl>
      {result.metrics.length > 0 && (
        <ul className="space-y-1 text-xs">
          {result.metrics.map((m) => (
            <li key={m.name} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-foreground">{m.name}</span>
              <span className="font-mono text-muted-foreground">{`${m.value ?? '—'} ≥ ${m.threshold ?? '—'}`}</span>
              <Badge tone={m.passed ? 'success' : 'danger'}>{m.passed ? '達標' : '未達標'}</Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
