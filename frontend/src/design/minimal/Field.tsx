import type { InputProps, SelectProps, TextareaProps } from '../contract'
import { cn } from '../../lib/cn'

const field = [
  'w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground',
  'placeholder:text-muted-foreground transition-colors duration-200',
  'focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
  'disabled:cursor-not-allowed disabled:opacity-50',
]

/** 文字輸入框（高度 44px）。 */
export function Input({ className, ...props }: InputProps) {
  return <input className={cn(field, 'h-11', className)} {...props} />
}

/** 下拉選單（高度 44px）。 */
export function Select({ className, ...props }: SelectProps) {
  return <select className={cn(field, 'h-11 pr-8', className)} {...props} />
}

/** 多行輸入框。 */
export function Textarea({ className, ...props }: TextareaProps) {
  return <textarea className={cn(field, 'min-h-20 py-2 leading-relaxed', className)} {...props} />
}
