import { MotionConfig } from 'framer-motion'

import { AdminApp } from './admin/AdminApp'
import { usePath } from './admin/router'
import { DemoBanner } from './DemoBanner'
import { PublicApp } from './public/PublicApp'
import { isAdminPath } from './public/routes'

/** `/admin` 以下是管理前端（需登入）；其他路徑都是公開網站（ADR-0015）。 */
export default function App() {
  const path = usePath()
  return (
    <MotionConfig reducedMotion="user">
      <DemoBanner />
      {isAdminPath(path) ? <AdminApp /> : <PublicApp />}
    </MotionConfig>
  )
}
