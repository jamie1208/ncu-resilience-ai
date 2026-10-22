/*
 * 兩套設計元件庫（minimal、playful）共同的介面（ADR-0014）。
 * 頁面只從 `@design` 匯入元件，只使用 REQUIRED_TOKENS 對應的 Tailwind 色彩與字體；
 * 各設計專屬的 token（例如 playful 的 secondary／tertiary）只能在該設計的元件內部使用。
 */
import type {
  ButtonHTMLAttributes,
  ComponentType,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

export type ButtonVariant = 'primary' | 'outline' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant | null
  size?: ButtonSize | null
}

export interface SectionLabelProps {
  children: ReactNode
  /** 裝飾圓點／形狀是否動畫；只用於裝飾，使用者要求減少動態時自動停止。 */
  pulse?: boolean
  className?: string
}

export interface CardProps extends HTMLAttributes<HTMLElement> {
  /** 輸出的語意標籤，預設 div。 */
  as?: 'div' | 'li' | 'article' | 'section'
  /** 強調卡片；不得用來表示風險等級（風險等級用語意色與文字）。 */
  featured?: boolean
  /** 可點擊的卡片才加 hover 效果。 */
  interactive?: boolean
}

/** 標籤色調：對應語意 token；風險等級、事件狀態必須搭配文字，不能只靠顏色。 */
export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone | null
}

export type InputProps = InputHTMLAttributes<HTMLInputElement>
export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>
export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>

/** 每套設計都必須匯出的元件。 */
export interface DesignLibrary {
  Badge: ComponentType<BadgeProps>
  Button: ComponentType<ButtonProps>
  Card: ComponentType<CardProps>
  Input: ComponentType<InputProps>
  SectionLabel: ComponentType<SectionLabelProps>
  Select: ComponentType<SelectProps>
  Textarea: ComponentType<TextareaProps>
}

/** 每套設計的 styles.css 都必須定義的語意 token（頁面只能用這些）。 */
export const REQUIRED_TOKENS = [
  '--color-background',
  '--color-foreground',
  '--color-muted',
  '--color-muted-foreground',
  '--color-accent',
  '--color-accent-foreground',
  '--color-border',
  '--color-card',
  '--color-ring',
  '--color-danger',
  '--color-danger-foreground',
  '--color-danger-muted',
  '--color-warning',
  '--color-warning-foreground',
  '--color-warning-muted',
  '--color-success',
  '--color-success-foreground',
  '--color-success-muted',
  '--color-info',
  '--color-info-foreground',
  '--color-info-muted',
  '--font-display',
  '--font-sans',
  '--font-mono',
] as const
