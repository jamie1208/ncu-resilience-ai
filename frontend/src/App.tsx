import { MotionConfig } from 'framer-motion'

import { AdminApp } from './admin/AdminApp'

/** 只有管理前端（ADR-0008）：根路徑為登入頁，登入後進入 /admin。 */
export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <AdminApp />
    </MotionConfig>
  )
}
