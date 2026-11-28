import { useEffect, useState } from 'react'
import { getIdToken } from 'firebase/auth'
import type { SessaoAutenticada } from './LoginPanel'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'
import './SemaforoTrabalhador.css'

type Alocacao = {
  contratoId: string
  semaforo: 'VERDE' | 'AMARELO' | 'VERMELHO'
  pendencias: Array<{ tipoNome: string; motivo: string }>
}
type DadosTrabalhador = { id: string; nome: string; alocacoes: Alocacao[] }
type Estado =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'success'; dados: DadosTrabalhador }

function ehAlocacao(value: unknown): value is Alocacao {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.contratoId === 'string' &&
    (v.semaforo === 'VERDE' || v.semaforo === 'AMARELO' || v.semaforo === 'VERMELHO') &&
    Array.isArray(v.pendencias) &&
    v.pendencias.every((p: unknown) => {
      if (!p || typeof p !== 'object') return false
      const item = p as Record<string, unknown>
      return typeof item.tipoNome === 'string' && typeof item.motivo === 'string'
    })
  )
}

function ehDetalhe(value: unknown): value is DadosTrabalhador {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.id === 'string' &&
    typeof v.nome === 'string' &&
    Array.isArray(v.alocacoes) &&
    v.alocacoes.every(ehAlocacao)
  )
}

const ESTADOS = {
  VERDE: { icon: '✅', label: 'Verde — regular' },
  AMARELO: { icon: '⚠️', label: 'Amarelo — atenção' },
  VERMELHO: { icon: '⛔', label: 'Vermelho — pendências' },
} as const

export function SemaforoTrabalhador({
  sessao,
  trabalhadorId,
}: {
  sessao: SessaoAutenticada
  trabalhadorId: string
}) {
  const [estado, setEstado] = useState<Estado>({ kind: 'loading' })
  const [tentativas, setTentativas] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    async function carregar() {
      setEstado({ kind: 'loading' })
      try {
        const token = await getIdToken(sessao.usuario)
        if (controller.signal.aborted) return
        const base = import.meta.env.VITE_API_URL?.trim().replace(/\/+$/, '') ?? ''
        const caminho = `/trabalhadores/${encodeURIComponent(trabalhadorId)}`
        const resposta = await fetch(base ? `${base}${caminho}` : `/api${caminho}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        if (!resposta.ok) {
          setEstado({
            kind: 'error',
            message: `Não foi possível consultar o semáforo (HTTP ${resposta.status}).`,
          })
          return
        }
        const dados: unknown = await resposta.json()
        if (controller.signal.aborted) return
        if (!ehDetalhe(dados)) {
          setEstado({
            kind: 'error',
            message: 'A API retornou dados incompletos sobre o semáforo.',
          })
          return
        }
        setEstado({ kind: 'success', dados })
      } catch {
        if (!controller.signal.aborted)
          setEstado({ kind: 'error', message: 'Erro ao consultar o semáforo.' })
      }
    }
    void carregar()
    return () => controller.abort()
  }, [sessao.usuario, trabalhadorId, tentativas])

  return (
    <section className="semaforo-trabalhador" aria-label="Semáforo do trabalhador">
      {estado.kind === 'loading' && <LoadingState message="Carregando semáforo..." />}
      {estado.kind === 'error' && (
        <ErrorState message={estado.message} onRetry={() => setTentativas((n) => n + 1)} />
      )}
      {estado.kind === 'success' && (
        <>
          <h3>Semáforo: {estado.dados.nome}</h3>
          {estado.dados.alocacoes.length === 0 && (
            <p role="status">Este trabalhador não possui alocações em contratos.</p>
          )}
          {estado.dados.alocacoes.map((alocacao) => (
            <div className="semaforo-trabalhador__alocacao" key={alocacao.contratoId}>
              <p>
                <strong>Contrato:</strong> {alocacao.contratoId}
              </p>
              <p className={`semaforo-trabalhador__${alocacao.semaforo.toLowerCase()}`}>
                <span aria-hidden="true">{ESTADOS[alocacao.semaforo].icon}</span>{' '}
                <strong>{ESTADOS[alocacao.semaforo].label}</strong>
              </p>
              {alocacao.pendencias.length > 0 && (
                <ul>
                  {alocacao.pendencias.map((pendencia, i) => (
                    <li key={`${pendencia.tipoNome}-${i}`}>
                      {pendencia.tipoNome}: {pendencia.motivo}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <button type="button" onClick={() => setTentativas((n) => n + 1)}>
            Atualizar semáforo
          </button>
        </>
      )}
    </section>
  )
}
