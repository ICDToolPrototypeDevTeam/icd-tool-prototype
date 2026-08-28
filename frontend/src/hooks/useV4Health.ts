import { useEffect, useState } from 'react'
import { checkV4Health } from '../api'

export function useV4Health() {
  const [isOnline, setIsOnline] = useState(true)
  const [v4Online, setV4Online] = useState(true)

  useEffect(() => {
    let mounted = true

    fetch('/api/health')
      .then((r) => {
        if (r.ok && mounted) setIsOnline(true)
      })
      .catch(() => {
        if (mounted) setIsOnline(false)
      })

    checkV4Health()
      .then((ok) => {
        if (mounted) setV4Online(ok)
      })
      .catch(() => {
        if (mounted) setV4Online(false)
      })

    return () => {
      mounted = false
    }
  }, [])

  return { isOnline, v4Online }
}
