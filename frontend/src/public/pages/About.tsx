import { Card } from '@design'

import { PageIntro } from '../ui'

const RULES = [
  '經官方來源證實（或站主人工查證）的事件，才會公開事件內容與小報告。',
  '未經查證的事件只公開中性的安全提醒，不代表事情確實發生。',
  '針對特定個人或單位的指控不會自動公開。',
  '小報告只寫影響與建議，不公開推測的原因。',
  '社群來源只顯示 AI 整理的摘要，不顯示社群貼文原文、連結或發文者資訊。',
  '公開內容會遮蔽姓名、電話等個人資料；有問題的內容可由站主立即下架。',
]

/** 關於本站：非官方身分、資料來源與公開規則（ADR-0015）。 */
export function About() {
  return (
    <div className="space-y-8">
      <PageIntro eyebrow="關於" title="關於本站" />

      <Card featured className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">這是個人專題</h2>
        <p className="text-base leading-relaxed text-foreground">
          NCU Resilience AI 是學生的個人專題，不是國立中央大學的官方系統，內容不代表學校立場，也不是通報管道。
          遇到緊急狀況請直接撥打 110 或 119，或聯絡學校校安中心。
        </p>
      </Card>

      <section aria-labelledby="about-what" className="space-y-3">
        <h2 id="about-what" className="text-lg font-semibold text-foreground">
          這個網站做什麼
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
          系統每天收集學校公告、新聞與社群上的校園訊息，由 AI 整理成事件、判斷影響輕重並提出建議，
          再追蹤後續的改善情形。站主在電腦開機時才提供服務，網站不保證隨時可用。
        </p>
      </section>

      <section aria-labelledby="about-rules" className="space-y-3">
        <h2 id="about-rules" className="text-lg font-semibold text-foreground">
          公開規則
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
          哪些內容可以公開由固定的程式規則判斷，不由 AI 決定：
        </p>
        <ul className="max-w-2xl list-disc space-y-2 pl-5 text-sm leading-relaxed text-foreground">
          {RULES.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}
