import { cva } from 'class-variance-authority'

import type { ButtonProps } from '../contract'
import { cn } from '../../lib/cn'

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-2 rounded-full border-2 font-bold whitespace-nowrap',
    'transition-all duration-300 ease-bounce',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
    'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
    'disabled:pointer-events-none disabled:opacity-50',
  ],
  {
    variants: {
      variant: {
        // 糖果按鈕：深色框 + 硬陰影；hover 往左上浮起、active 往右下按下
        primary: [
          'border-foreground bg-accent text-accent-foreground shadow-pop-sm sm:shadow-pop',
          'hover:-translate-x-0.5 hover:-translate-y-0.5 sm:hover:shadow-pop-hover',
          'active:translate-x-0.5 active:translate-y-0.5 active:shadow-pop-active',
        ],
        outline: 'border-foreground bg-transparent text-foreground hover:bg-tertiary',
        ghost: 'border-transparent text-foreground hover:border-foreground hover:bg-muted',
      },
      // 所有尺寸的觸控高度都至少 48px
      size: {
        sm: 'h-12 px-5 text-sm',
        md: 'h-12 px-7 text-sm',
        lg: 'h-14 px-8 text-base',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

/** 膠囊按鈕（primary 糖果／outline／ghost）。預設 `type="button"`，避免意外送出表單。 */
export function Button({ className, variant, size, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
