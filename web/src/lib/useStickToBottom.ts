import { useCallback, useLayoutEffect, useRef } from 'react'
import type { UIEvent } from 'react'

const NEAR_BOTTOM_PX = 48

/** Keeps a scrolling box at its latest line whenever `content` changes, unless the reader has scrolled up to read.
 * Returns the box's ref, its scroll handler, and `stick()` to follow the latest line again (after sending, say). */
export function useStickToBottom<T extends HTMLElement>(content: unknown) {
  const box = useRef<T>(null)
  const pinned = useRef(true)
  useLayoutEffect(() => {
    const el = box.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }, [content])
  const onScroll = useCallback((event: UIEvent<T>) => {
    const el = event.currentTarget
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
  }, [])
  const stick = useCallback(() => { pinned.current = true }, [])
  return { ref: box, onScroll, stick }
}
