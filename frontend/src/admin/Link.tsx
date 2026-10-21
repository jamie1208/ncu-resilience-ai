import type { AnchorHTMLAttributes, MouseEvent } from 'react'

import { navigate } from './router'

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
}

/** 站內連結：一般點擊走前端路由，修飾鍵（新分頁等）交給瀏覽器。 */
export function Link({ to, onClick, ...props }: LinkProps) {
  function handleClick(e: MouseEvent<HTMLAnchorElement>) {
    onClick?.(e)
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return
    }
    e.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={handleClick} {...props} />
}
