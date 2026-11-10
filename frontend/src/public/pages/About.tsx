import { Card } from '@design'

import { PageIntro } from '../ui'

const RULES = [
  '每則快報都會標示查證狀態：只有經官方來源證實或站主人工查證的事件標「已查證」，其他一律標「尚未查證」，不代表事情確實發生。',
  '提到特定個人或單位的內容照 AI 摘要公開，站主會立即收到提醒，有問題時立即下架。',
  '同類型的事件會放進同一個系列分析頁；AI 看到的規律與可能原因只出現在系列分析頁，一律標示「推測，尚未查證」，經站主確認的才標「已確認」。',
  '件數、每週趨勢與改善前後比較由程式計算，不由 AI 決定；件數變少不一定是措施的功勞。',
  '社群來源與匿名回報只顯示 AI 整理的摘要，不顯示社群貼文原文、連結或發文者資訊，匿名回報原文永不公開。',
  '公開內容會遮蔽電話、Email 等個人資料；有問題的內容可由站主立即下架。',
]

/** 關於本站：非官方身分、資料來源與公開規則（ADR-0015；P8-T06 起依 ADR-0018、ADR-0020）。 */
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
          每天，系統會收集學校公告、新聞與社群上的校園訊息，由 AI 整理成一則則快報；同類型的事件放進同一個系列，
          看它多常發生、提出改善建議，再一路追蹤改善情形。站主的電腦開機時才提供服務，網站不保證隨時可用。
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
