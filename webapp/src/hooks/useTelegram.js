import { useEffect, useState } from 'react'

/**
 * Hook for Telegram Mini App SDK
 *
 * Provides access to:
 * - tg: WebApp instance
 * - user: Current user info
 * - initData: Raw init data for API auth
 */
export function useTelegram() {
  const [tg, setTg] = useState(null)
  const [user, setUser] = useState(null)
  const [initData, setInitData] = useState('')

  useEffect(() => {
    const telegram = window.Telegram?.WebApp

    if (telegram) {
      setTg(telegram)
      setUser(telegram.initDataUnsafe?.user || null)
      setInitData(telegram.initData || '')

      // Apply theme
      document.documentElement.style.setProperty(
        '--tg-theme-bg-color',
        telegram.themeParams.bg_color || '#ffffff'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-text-color',
        telegram.themeParams.text_color || '#000000'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-hint-color',
        telegram.themeParams.hint_color || '#999999'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-link-color',
        telegram.themeParams.link_color || '#2481cc'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-button-color',
        telegram.themeParams.button_color || '#2481cc'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-button-text-color',
        telegram.themeParams.button_text_color || '#ffffff'
      )
      document.documentElement.style.setProperty(
        '--tg-theme-secondary-bg-color',
        telegram.themeParams.secondary_bg_color || '#f0f0f0'
      )
    }
  }, [])

  return { tg, user, initData }
}
