import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'

import { cn } from '../lib/cn'

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 rounded-xl font-medium whitespace-nowrap',
    'transition-all duration-200 ease-out active:scale-[0.98]',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
    'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
    'disabled:pointer-events-none disabled:opacity-50',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-linear-to-r from-accent to-accent-secondary text-accent-foreground shadow-sm',
          'hover:-translate-y-0.5 hover:shadow-accent-lg hover:brightness-110',
        ],
        outline: [
          'border border-border bg-transparent text-foreground',
          'hover:border-accent/30 hover:bg-muted hover:shadow-sm',
        ],
        ghost: 'text-muted-foreground hover:bg-muted hover:text-foreground',
      },
      // 所有尺寸的觸控高度都至少 44px
      size: {
        sm: 'h-11 px-4 text-sm',
        md: 'h-12 px-6 text-sm',
        lg: 'h-14 px-8 text-base',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

/** 基礎按鈕（primary／outline／ghost）。預設 `type="button"`，避免意外送出表單。 */
export function Button({ className, variant, size, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
