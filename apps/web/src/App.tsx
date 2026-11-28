import { useEffect, useState } from 'react'
import { ErrorState } from './components/ErrorState'
import { LoadingState } from './components/LoadingState'
import { LoginPanel, type SessaoAutenticada } from './components/LoginPanel'
import { TrabalhadoresPanel } from './components/TrabalhadoresPanel'
import { AprovacoesPanel } from './components/AprovacoesPanel'

type HealthState =
  { kind: 'loading' } | { kind: 'success'; status: string } | { kind: 'error'; message: string }

function isHealthResponse(data: unknown): data is { status: string } {
  return (
    typeof data === 'object' && data !== null && 'status' in data && typeof data.status === 'string'
  )
}

function HealthBlock() {
  const [state, setState] = useState<HealthState>({ kind: 'loading' })
  const [tentativas, setTentativas] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    async function verificarAPI(): Promise<void> {
      try {
        const base = import.meta.env.VITE_API_URL?.replace(/\/+$/, '') ?? ''
        const response = await fetch(base ? `${base}/healthz` : '/api/healthz', {
          signal: controller.signal,
        })
        if (!response.ok) {
          setState({ kind: 'error', message: `A API respondeu com HTTP ${response.status}.` })
          return
        }
        const data: unknown = await response.json()
        if (!isHealthResponse(data)) {
          setState({ kind: 'error', message: 'Resposta da API em formato inesperado.' })
          return
        }
        setState({ kind: 'success', status: data.status })
      } catch {
        if (!controller.signal.aborted) {
          setState({ kind: 'error', message: 'Não foi possível conectar à API.' })
        }
      }
    }
    void verificarAPI()
    return () => controller.abort()
  }, [tentativas])

  function tentarNovamente() {
    setState({ kind: 'loading' })
    setTentativas((atual) => atual + 1)
  }

  return (
    <section aria-live="polite" aria-label="Estado da API">
      <h2>Estado da API</h2>
      {state.kind === 'loading' && <LoadingState message="Verificando conexão com a API..." />}
      {state.kind === 'success' && <p>API no ar: {state.status}</p>}
      {state.kind === 'error' && <ErrorState message={state.message} onRetry={tentarNovamente} />}
    </section>
  )
}

export default function App() {
  const [sessao, setSessao] = useState<SessaoAutenticada | null>(null)

  return (
    <main>
      <h1>Habilita</h1>
      <HealthBlock />
      <LoginPanel onSessionChange={setSessao} />
      {sessao && sessao.papel !== 'porteiro' && (
        <TrabalhadoresPanel key={sessao.usuario.uid} sessao={sessao} />
      )}
      {sessao && (sessao.papel === 'admin_contratante' || sessao.papel === 'analista_sst') && (
        <AprovacoesPanel key={sessao.usuario.uid} sessao={sessao} />
      )}
      {sessao && sessao.papel === 'porteiro' && (
        <p role="status">A jornada de trabalhadores não está disponível para o perfil porteiro.</p>
      )}
    </main>
  )
}
