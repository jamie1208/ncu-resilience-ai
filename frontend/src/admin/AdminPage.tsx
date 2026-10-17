const modules = [
  { title: '事件工作台', body: '來源對照、風險分項、證據清單與更新歷史。', task: 'P3-T05' },
  { title: '來源健康', body: '各來源擷取紀錄、失敗重試與健康狀態。', task: 'P1-T10' },
  { title: '稽核紀錄', body: '寫入操作與敏感存取的不可修改紀錄。', task: 'P0-T07' },
]

/** 管理後台（骨架）：以資訊密度為主，不使用裝飾元素與持續性動畫。 */
export function AdminPage() {
  return (
    <div className="min-h-screen bg-muted">
      <header className="border-b border-border bg-card">
        <div className="flex items-center justify-between px-4 py-3 sm:px-6">
          <h1 className="text-base font-semibold">NCU Resilience AI 管理後台</h1>
          <a
            href="/"
            className="inline-flex h-11 items-center rounded-lg px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            回公開網站
          </a>
        </div>
      </header>
      <main className="px-4 py-6 sm:px-6">
        <p className="text-sm text-muted-foreground">登入與權限控管將於 P3-T01 加入；目前僅供開發。</p>
        <ul className="mt-4 grid gap-4 md:grid-cols-3">
          {modules.map((m) => (
            <li key={m.title} className="rounded-xl border border-border bg-card p-4 shadow-sm">
              <h2 className="text-sm font-semibold">{m.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{m.body}</p>
              <p className="mt-3 font-mono text-xs text-muted-foreground">{m.task}</p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}
