import { useEffect, useState } from 'react'
import { getIdToken } from 'firebase/auth'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'
import type { SessaoAutenticada } from './LoginPanel'
import './TrabalhadoresPanel.css'

type Trabalhador = {
  id: string
  terceirizadaId: string
  nome: string
  cpfMascarado: string
  ativo: boolean
}

type PaginaTrabalhadores = {
  itens: Trabalhador[]
  proximoCursor: string | null
}

type Estado =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'success'; pagina: PaginaTrabalhadores }

function eTrabalhador(valor: unknown): valor is Trabalhador {
  if (typeof valor !== 'object' || valor === null) return false
  const v = valor as Record<string, unknown>
  return (
    typeof v.id === 'string' &&
    typeof v.terceirizadaId === 'string' &&
    typeof v.nome === 'string' &&
    typeof v.cpfMascarado === 'string' &&
    typeof v.ativo === 'boolean'
  )
}

function ePaginaTrabalhadores(valor: unknown): valor is PaginaTrabalhadores {
  if (typeof valor !== 'object' || valor === null) return false
  const v = valor as Record<string, unknown>
  return (
    Array.isArray(v.itens) &&
    v.itens.every(eTrabalhador) &&
    (v.proximoCursor === null || typeof v.proximoCursor === 'string')
  )
}

function mensagemErro(status: number): string {
  if (status === 401) return 'Sua sessão expirou. Saia e entre novamente.'
  if (status === 403) return 'Seu perfil não tem permissão para listar trabalhadores.'
  if (status === 503) return 'A API está temporariamente indisponível. Tente novamente.'
  return `Não foi possível listar os trabalhadores (HTTP ${status}).`
}

export function TrabalhadoresPanel({ sessao }: { sessao: SessaoAutenticada }) {
  const [estado, setEstado] = useState<Estado>({ kind: 'loading' })
  const [cursores, setCursores] = useState<Array<string | null>>([null])
  const [indice, setIndice] = useState(0)
  const [tentativas, setTentativas] = useState(0)
  const cursor = cursores[indice] ?? null

  useEffect(() => {
    const controller = new AbortController()

    async function consultar(): Promise<void> {
      try {
        const token = await getIdToken(sessao.usuario)
        if (controller.signal.aborted) return

        const base = import.meta.env.VITE_API_URL?.trim().replace(/\/+$/, '') ?? ''
        const url = new URL(
          base ? `${base}/trabalhadores` : '/api/trabalhadores',
          window.location.origin,
        )
        url.searchParams.set('limite', '20')
        if (cursor) url.searchParams.set('cursor', cursor)

        const response = await fetch(url, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        })
        if (!response.ok) {
          if (!controller.signal.aborted) {
            setEstado({ kind: 'error', message: mensagemErro(response.status) })
          }
          return
        }

        const dados: unknown = await response.json()
        if (controller.signal.aborted) return
        if (!ePaginaTrabalhadores(dados)) {
          setEstado({ kind: 'error', message: 'A API retornou uma lista em formato inesperado.' })
          return
        }
        setEstado({ kind: 'success', pagina: dados })
      } catch {
        if (!controller.signal.aborted) {
          setEstado({
            kind: 'error',
            message: 'Não foi possível conectar à API. Verifique sua conexão e tente novamente.',
          })
        }
      }
    }

    void consultar()
    return () => controller.abort()
  }, [sessao.usuario, cursor, tentativas])

  function tentarNovamente() {
    setEstado({ kind: 'loading' })
    setTentativas((valor) => valor + 1)
  }

  function proximaPagina(proximoCursor: string) {
    setCursores((atual) => [...atual.slice(0, indice + 1), proximoCursor])
    setIndice((atual) => atual + 1)
    setEstado({ kind: 'loading' })
  }

  function paginaAnterior() {
    if (indice <= 0) return
    setIndice((atual) => atual - 1)
    setEstado({ kind: 'loading' })
  }

  return (
    <section className="trabalhadores-panel" aria-labelledby="j2-title">
      <div className="trabalhadores-panel__header">
        <div>
          <p className="trabalhadores-panel__eyebrow">Jornada J2</p>
          <h2 id="j2-title">Trabalhadores</h2>
          <p>Dados consultados diretamente na API do Habilita.</p>
        </div>
        <button type="button" onClick={tentarNovamente} disabled={estado.kind === 'loading'}>
          Atualizar
        </button>
      </div>

      {estado.kind === 'loading' && <LoadingState message="Carregando trabalhadores..." />}
      {estado.kind === 'error' && <ErrorState message={estado.message} onRetry={tentarNovamente} />}
      {estado.kind === 'success' && estado.pagina.itens.length === 0 && (
        <p role="status" className="trabalhadores-panel__empty">
          Nenhum trabalhador encontrado nesta página.
        </p>
      )}
      {estado.kind === 'success' && estado.pagina.itens.length > 0 && (
        <div className="trabalhadores-panel__table-scroll">
          <table>
            <caption>Trabalhadores cadastrados — página {indice + 1}</caption>
            <thead>
              <tr>
                <th scope="col">Nome</th>
                <th scope="col">CPF</th>
                <th scope="col">Terceirizada</th>
                <th scope="col">Situação</th>
              </tr>
            </thead>
            <tbody>
              {estado.pagina.itens.map((trabalhador) => (
                <tr key={trabalhador.id}>
                  <td>{trabalhador.nome}</td>
                  <td>{trabalhador.cpfMascarado}</td>
                  <td>{trabalhador.terceirizadaId}</td>
                  <td>
                    <span className={trabalhador.ativo ? 'status-ativo' : 'status-inativo'}>
                      {trabalhador.ativo ? 'Ativo' : 'Inativo'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {estado.kind === 'success' && (
        <nav className="trabalhadores-panel__pagination" aria-label="Paginação de trabalhadores">
          <button type="button" onClick={paginaAnterior} disabled={indice === 0}>
            Anterior
          </button>
          <span>Página {indice + 1}</span>
          <button
            type="button"
            disabled={!estado.pagina.proximoCursor}
            onClick={() => {
              if (estado.pagina.proximoCursor) proximaPagina(estado.pagina.proximoCursor)
            }}
          >
            Próxima
          </button>
        </nav>
      )}
    </section>
  )
}
