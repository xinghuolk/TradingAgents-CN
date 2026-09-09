import { getCurrentScope, onScopeDispose, ref } from 'vue'

export type ResearchSaveState = 'saved' | 'dirty' | 'saving' | 'failed'

export function useResearchAutosave<T>(save: (value: T) => Promise<unknown>, delayMs = 1500) {
  const state = ref<ResearchSaveState>('saved')
  let requested = 0
  let completed = 0
  let latest: T
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending: Promise<boolean> | undefined

  function clearTimer() {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }

  function schedule(value: T) {
    latest = value
    requested += 1
    state.value = pending ? 'saving' : 'dirty'
    clearTimer()
    timer = setTimeout(() => flush(), delayMs)
  }

  function flush(): Promise<boolean> {
    clearTimer()
    if (pending) return pending
    if (completed === requested) return Promise.resolve(true)
    // One drain owns all writes; server responses never overwrite newer local input.
    pending = (async () => {
      while (completed !== requested) {
        const version = requested
        const value = latest
        state.value = 'saving'
        try {
          await save(value)
          completed = version
        } catch {
          clearTimer()
          state.value = 'failed'
          return false
        }
      }
      clearTimer()
      state.value = 'saved'
      return true
    })().finally(() => {
      pending = undefined
    })
    return pending
  }

  function dispose() {
    clearTimer()
  }

  if (getCurrentScope()) onScopeDispose(dispose)
  return { state, schedule, flush, retry: flush, dispose }
}
