import { MotionConfig } from 'framer-motion'

import { AdminPage } from './admin/AdminPage'
import { HomePage } from './public/HomePage'

function isAdminPath(pathname: string): boolean {
  return pathname === '/admin' || pathname.startsWith('/admin/')
}

/** 依路徑切換公開前端與管理後台；正式路由於 P3 前端任務決定。 */
export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      {isAdminPath(window.location.pathname) ? <AdminPage /> : <HomePage />}
    </MotionConfig>
  )
}
