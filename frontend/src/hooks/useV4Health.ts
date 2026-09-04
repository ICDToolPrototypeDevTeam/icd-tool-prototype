import { useEffect, useState } from 'react'
import { checkV4Health } from '../api'

export function useV4Health() {
  const [v4Online, setV4Online] = useState(true)

  useEffect(() => {
    let mounted = true

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

  return { v4Online }
}
