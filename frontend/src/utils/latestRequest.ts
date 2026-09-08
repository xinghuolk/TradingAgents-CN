export interface LatestRequestHandlers<T> {
  onSuccess: (value: T) => void
  onError: (error: unknown) => void
  onSettled: () => void
}

export interface LatestRequestCoordinator {
  <T>(request: () => Promise<T>, handlers: LatestRequestHandlers<T>): Promise<void>
}

export function createLatestRequestCoordinator(): LatestRequestCoordinator {
  let latestToken = 0

  return async function coordinate<T>(
    request: () => Promise<T>,
    handlers: LatestRequestHandlers<T>
  ): Promise<void> {
    const requestToken = ++latestToken
    try {
      const value = await request()
      if (requestToken === latestToken) {
        handlers.onSuccess(value)
      }
    } catch (error) {
      if (requestToken === latestToken) {
        handlers.onError(error)
      }
    } finally {
      if (requestToken === latestToken) {
        handlers.onSettled()
      }
    }
  }
}
