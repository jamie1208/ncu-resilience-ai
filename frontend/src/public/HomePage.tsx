import { motion, type Variants } from 'framer-motion'

import { Button } from '../components/Button'
import { SectionLabel } from '../components/SectionLabel'

const easeOut = [0.16, 1, 0.3, 1] as const

const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.1, delayChildren: 0.1 } },
}

const fadeInUp: Variants = {
  hidden: { opacity: 0, y: 28 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.7, ease: easeOut } },
}

const steps = [
  { title: '彙整公開來源', body: '只擷取已核准的校方公告與政府開放資料，保留原文連結與擷取時間。' },
  { title: 'AI 協助整理', body: '將公告整理成事件，所有判斷都對應到可查證的來源；證據不足時不下結論。' },
  { title: '查證後才發布', body: '未經核實的具體指控不公開；安全提醒只使用中性、一般性的說明。' },
]

/** 公開前端首頁（骨架）。完整事件列表與詳細頁於 P3-T04 實作。 */
export function HomePage() {
  return (
    <div className="min-h-screen overflow-x-hidden">
      {/* 緊急提示：最高優先，不加任何動畫或漸層 */}
      <aside
        role="note"
        aria-label="緊急聯絡提示"
        className="border-b border-danger/20 bg-danger-muted px-4 py-3 text-sm text-danger"
      >
        <p className="mx-auto max-w-6xl font-medium">
          緊急狀況請直接撥打 110（警察）或 119（消防、救護），或聯絡學校校安中心；本系統不是緊急通報管道。
        </p>
      </aside>

      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-6 sm:px-6">
        <span className="font-display text-xl">NCU Resilience AI</span>
        <span className="font-mono text-xs text-muted-foreground">研究專題 · Beta</span>
      </header>

      <main>
        <section className="relative mx-auto max-w-6xl px-4 py-20 sm:px-6 lg:py-28">
          {/* 裝飾光暈：小螢幕隱藏 */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-24 right-0 hidden h-96 w-96 rounded-full bg-accent/5 blur-[150px] lg:block"
          />
          <motion.div initial="hidden" animate="visible" variants={stagger} className="relative max-w-3xl">
            <motion.div variants={fadeInUp}>
              <SectionLabel pulse>校園韌性資訊</SectionLabel>
            </motion.div>
            <motion.h1
              variants={fadeInUp}
              className="mt-8 font-display text-[2.75rem] leading-[1.1] tracking-[-0.02em] sm:text-6xl"
            >
              看清校園動態，<span className="gradient-text">及早準備</span>
            </motion.h1>
            <motion.p variants={fadeInUp} className="mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground">
              彙整中央大學公開公告與氣象等開放資料，整理成可查證的校園事件與安全提醒。
            </motion.p>
            <motion.div variants={fadeInUp} className="mt-10 flex flex-col gap-4 sm:flex-row">
              <Button size="lg" className="w-full sm:w-auto" disabled>
                校園動態（即將推出）
              </Button>
              <Button size="lg" variant="outline" className="w-full sm:w-auto" disabled>
                安全提醒（即將推出）
              </Button>
            </motion.div>
          </motion.div>
        </section>

        {/* 反轉深色區塊 */}
        <section className="bg-dot-pattern bg-foreground text-background">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 lg:py-28">
            <h2 className="font-display text-3xl sm:text-4xl">資訊怎麼來、怎麼用</h2>
            <ol className="mt-12 grid gap-8 md:grid-cols-3">
              {steps.map((step, i) => (
                <li key={step.title} className="rounded-2xl border border-white/10 bg-white/5 p-6">
                  <span className="font-mono text-sm text-accent-secondary">0{i + 1}</span>
                  <h3 className="mt-3 text-lg font-semibold tracking-[-0.01em]">{step.title}</h3>
                  <p className="mt-2 leading-relaxed text-background/80">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* 非官方免責聲明：清楚可見，不受裝飾弱化 */}
        <section aria-labelledby="about-heading" className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
          <div className="rounded-2xl border border-border bg-card p-6 shadow-md sm:p-8">
            <h2 id="about-heading" className="text-xl font-semibold">
              關於本系統
            </h2>
            <p className="mt-3 leading-relaxed text-foreground">
              本系統為國立中央大學學生研究專題，非校方官方系統，內容不代表學校立場；正式公告請以各處室網站為準。
            </p>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <p className="mx-auto max-w-6xl px-4 py-8 text-sm text-muted-foreground sm:px-6">
          NCU Resilience AI · 研究專題
        </p>
      </footer>
    </div>
  )
}
