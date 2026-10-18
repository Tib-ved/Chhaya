import { useEffect, useRef, useState } from 'react'

/** One read when the page opens. Reloading the page reads again; nothing runs on a timer. */
export function useLoad<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const latest = useRef(fetcher)
  latest.current = fetcher

  useEffect(() => {
    let active = true
    latest.current().then(
      result => { if (active) { setData(result); setError(null); setIsLoading(false) } },
      (err: unknown) => { if (active) { setError(err instanceof Error ? err : new Error(String(err))); setIsLoading(false) } },
    )
    return () => { active = false }
  }, [])

  return { data, error, isLoading, isStale: error !== null }
}
