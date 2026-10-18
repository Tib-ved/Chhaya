import { useEffect, useRef, useState } from 'react'

export interface PollResult<T> {
  data: T | null
  error: Error | null
  isLoading: boolean
  isStale: boolean
}

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean = true
): PollResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [isStale, setIsStale] = useState<boolean>(false)

  const activeRef = useRef(enabled)
  activeRef.current = enabled
  const latest = useRef(fetcher)
  latest.current = fetcher

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false)
      return
    }

    let isMounted = true
    let timer: number | null = null

    let attempted = false

    async function execute() {
      // Pause poll if document is hidden to conserve bandwidth/battery; the first read always runs
      if (document.hidden && attempted) {
        if (isMounted && activeRef.current) {
          timer = window.setTimeout(execute, intervalMs)
        }
        return
      }
      attempted = true

      try {
        const result = await latest.current()
        if (isMounted) {
          setData(result)
          setError(null)
          setIsStale(false)
          setIsLoading(false)
        }
      } catch (err: unknown) {
        if (isMounted) {
          setError(err instanceof Error ? err : new Error(String(err)))
          setIsStale(true) // keep last good data, mark as stale
          setIsLoading(false)
        }
      }

      if (isMounted && activeRef.current) {
        timer = window.setTimeout(execute, intervalMs)
      }
    }

    execute()

    return () => {
      isMounted = false
      if (timer) clearTimeout(timer)
    }
  }, [intervalMs, enabled])

  return { data, error, isLoading, isStale }
}
