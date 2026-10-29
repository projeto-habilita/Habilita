import { useEffect, useState } from 'react'
import { ErrorState } from './components/ErrorState'

type HealthState =
  { kind: 'loading' } | { kind: 'success'; status: string } | { kind: 'error'; message: string }

function isHealthResponse(data: unknown): data is { status: string } {
  return (
    typeof data === 'object' && data !== null && 'status' in data && typeof data.status === 'string'
  )
}

function HealthBlock() {
  const [state, setState] = useState<HealthState>({
    kind: 'loading',
  })

  const [tentativas, setTentativas] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    async function verificarAPI(): Promise<void> {
      try {
        const response = await fetch('/api/healthz', {
          signal: controller.signal,
        })

        if (controller.signal.aborted) return

        if (!response.ok) {
          setState({
            kind: 'error',
            message:
              `A API respondeu com HTTP ${response.status}. ` +
              'Verifique o serviço e tente novamente.',
          })
          return
        }

        const data: unknown = await response.json()

        if (controller.signal.aborted) return

        if (!isHealthResponse(data)) {
          setState({
            kind: 'error',
            message: 'A API retornou dados em formato inesperado.',
          })
          return
        }

        setState({
          kind: 'success',
          status: data.status,
        })
      } catch {
        if (controller.signal.aborted) return

        setState({
          kind: 'error',
          message: 'Não foi possível conectar à API. ' + 'Verifique sua conexão e tente novamente.',
        })
      }
    }

    void verificarAPI()

    return () => {
      controller.abort()
    }
  }, [tentativas])

  function tentarNovamente() {
    setState({ kind: 'loading' })
    setTentativas((atual) => atual + 1)
  }

  return (
    <section aria-live="polite">
      <h2>Estado da API</h2>

      {state.kind === 'loading' && <p>Carregando informações...</p>}

      {state.kind === 'success' && <p>API no ar: {state.status}</p>}

      {state.kind === 'error' && <ErrorState message={state.message} onRetry={tentarNovamente} />}
    </section>
  )
}

export default function App() {
  return (
    <main>
      <h1>Habilita</h1>
      <HealthBlock />
    </main>
  )
}
