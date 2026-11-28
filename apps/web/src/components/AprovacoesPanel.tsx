import { useEffect, useState, type FormEvent } from 'react'
import { getIdToken } from 'firebase/auth'
import type { SessaoAutenticada } from './LoginPanel'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'
import './AprovacoesPanel.css'

type Documento = {
  id: string
  trabalhadorId: string
  tipoDocumentoId: string
  validoAte: string
  statusRevisao: 'PENDENTE' | 'APROVADO' | 'REJEITADO'
  enviadoEm: string
}

type Pagina = { itens: Documento[]; proximoCursor: string | null }
type Estado =
  { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'success'; pagina: Pagina }

function ehDocumento(valor: unknown): valor is Documento {
  if (!valor || typeof valor !== 'object') return false
  const item = valor as Record<string, unknown>
  return (
    typeof item.id === 'string' &&
    typeof item.trabalhadorId === 'string' &&
    typeof item.tipoDocumentoId === 'string' &&
    typeof item.validoAte === 'string' &&
    typeof item.enviadoEm === 'string' &&
    (item.statusRevisao === 'PENDENTE' ||
      item.statusRevisao === 'APROVADO' ||
      item.statusRevisao === 'REJEITADO')
  )
}

function ehPagina(valor: unknown): valor is Pagina {
  if (!valor || typeof valor !== 'object') return false
  const pagina = valor as Record<string, unknown>
  return (
    Array.isArray(pagina.itens) &&
    pagina.itens.every(ehDocumento) &&
    (pagina.proximoCursor === null || typeof pagina.proximoCursor === 'string')
  )
}

function urlApi(caminho: string): string {
  const base = import.meta.env.VITE_API_URL?.trim().replace(/\/+$/, '') ?? ''
  return base ? `${base}${caminho}` : `/api${caminho}`
}

function mensagemErro(status: number): string {
  if (status === 401) return 'Sua sessão não foi aceita pela API. Entre novamente.'
  if (status === 403) return 'Seu perfil não pode revisar documentos.'
  if (status === 404) return 'O documento não foi encontrado ou está fora do seu acesso.'
  if (status === 409) return 'Este documento já foi revisado. Atualize a fila.'
  if (status === 503) return 'A API está temporariamente indisponível.'
  return `Não foi possível concluir a operação (HTTP ${status}).`
}

export function AprovacoesPanel({ sessao }: { sessao: SessaoAutenticada }) {
  const [estado, setEstado] = useState<Estado>({ kind: 'loading' })
  const [cursores, setCursores] = useState<Array<string | null>>([null])
  const [indice, setIndice] = useState(0)
  const [atualizacoes, setAtualizacoes] = useState(0)
  const [selecionado, setSelecionado] = useState<string | null>(null)
  const [motivo, setMotivo] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [erroAcao, setErroAcao] = useState('')
  const [sucesso, setSucesso] = useState('')
  const [urlVisualizacao, setUrlVisualizacao] = useState('')
  const cursor = cursores[indice] ?? null

  useEffect(() => {
    const controller = new AbortController()
    async function carregar(): Promise<void> {
      setEstado({ kind: 'loading' })
      try {
        const token = await getIdToken(sessao.usuario)
        if (controller.signal.aborted) return
        const params = new URLSearchParams({ statusRevisao: 'PENDENTE', limite: '20' })
        if (cursor) params.set('cursor', cursor)
        const resposta = await fetch(urlApi(`/documentos?${params.toString()}`), {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        if (!resposta.ok) {
          setEstado({ kind: 'error', message: mensagemErro(resposta.status) })
          return
        }
        const dados: unknown = await resposta.json()
        if (controller.signal.aborted) return
        if (!ehPagina(dados)) {
          setEstado({ kind: 'error', message: 'A API retornou uma fila em formato inesperado.' })
          return
        }
        setEstado({ kind: 'success', pagina: dados })
      } catch {
        if (!controller.signal.aborted) {
          setEstado({ kind: 'error', message: 'Não foi possível conectar à API.' })
        }
      }
    }
    void carregar()
    return () => controller.abort()
  }, [sessao.usuario, cursor, atualizacoes])

  function atualizar() {
    setErroAcao('')
    setSucesso('')
    setAtualizacoes((numero) => numero + 1)
  }

  function selecionar(id: string) {
    setSelecionado(id === selecionado ? null : id)
    setMotivo('')
    setErroAcao('')
    setSucesso('')
    setUrlVisualizacao('')
  }

  async function requisitar(caminho: string, corpo?: { motivo: string }): Promise<Response> {
    const token = await getIdToken(sessao.usuario)
    return fetch(urlApi(caminho), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(corpo ? { 'Content-Type': 'application/json' } : {}),
      },
      body: corpo ? JSON.stringify(corpo) : undefined,
    })
  }

  async function revisar(acao: 'aprovacao' | 'rejeicao') {
    if (!selecionado || ocupado) return
    const justificativa = motivo.trim()
    if (acao === 'rejeicao' && (justificativa.length < 10 || justificativa.length > 500)) {
      setErroAcao('Informe uma justificativa entre 10 e 500 caracteres.')
      return
    }
    setOcupado(true)
    setErroAcao('')
    setSucesso('')
    try {
      const resposta = await requisitar(
        `/documentos/${encodeURIComponent(selecionado)}/${acao}`,
        acao === 'rejeicao' ? { motivo: justificativa } : undefined,
      )
      if (!resposta.ok) {
        setErroAcao(mensagemErro(resposta.status))
        return
      }
      setSucesso(acao === 'aprovacao' ? 'Documento aprovado.' : 'Documento rejeitado.')
      setSelecionado(null)
      setMotivo('')
      setUrlVisualizacao('')
      setCursores([null])
      setIndice(0)
      setAtualizacoes((numero) => numero + 1)
    } catch {
      setErroAcao('Falha de conexão. Confira se a operação foi registrada antes de repetir.')
    } finally {
      setOcupado(false)
    }
  }

  async function obterVisualizacao() {
    if (!selecionado || ocupado) return
    setOcupado(true)
    setErroAcao('')
    setUrlVisualizacao('')
    try {
      const resposta = await requisitar(
        `/documentos/${encodeURIComponent(selecionado)}/url-download`,
      )
      if (!resposta.ok) {
        setErroAcao(mensagemErro(resposta.status))
        return
      }
      const dados: unknown = await resposta.json()
      if (
        !dados ||
        typeof dados !== 'object' ||
        !('url' in dados) ||
        typeof dados.url !== 'string'
      ) {
        setErroAcao('A API retornou um link inválido.')
        return
      }
      const link = new URL(dados.url)
      if (!['http:', 'https:'].includes(link.protocol)) {
        setErroAcao('O link do documento utiliza um protocolo não permitido.')
        return
      }
      setUrlVisualizacao(link.href)
    } catch {
      setErroAcao('Não foi possível obter o link temporário do documento.')
    } finally {
      setOcupado(false)
    }
  }

  function enviarRejeicao(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void revisar('rejeicao')
  }

  return (
    <section className="aprovacoes-panel" aria-labelledby="j3-title">
      <div className="aprovacoes-panel__header">
        <div>
          <p className="aprovacoes-panel__eyebrow">Jornada J3</p>
          <h2 id="j3-title">Fila de aprovação</h2>
          <p>Documentos pendentes de revisão pela equipe autorizada.</p>
        </div>
        <button type="button" onClick={atualizar} disabled={ocupado || estado.kind === 'loading'}>
          Atualizar
        </button>
      </div>

      {sucesso && (
        <p role="status" className="aprovacoes-panel__success">
          {sucesso}
        </p>
      )}
      {estado.kind === 'loading' && <LoadingState message="Carregando documentos pendentes..." />}
      {estado.kind === 'error' && <ErrorState message={estado.message} onRetry={atualizar} />}
      {estado.kind === 'success' && estado.pagina.itens.length === 0 && (
        <p role="status" className="aprovacoes-panel__empty">
          Não há documentos pendentes nesta página.
        </p>
      )}
      {estado.kind === 'success' && estado.pagina.itens.length > 0 && (
        <div className="aprovacoes-panel__scroll">
          <table>
            <caption>Documentos aguardando revisão — página {indice + 1}</caption>
            <thead>
              <tr>
                <th scope="col">Trabalhador</th>
                <th scope="col">Tipo</th>
                <th scope="col">Validade</th>
                <th scope="col">Ação</th>
              </tr>
            </thead>
            <tbody>
              {estado.pagina.itens.map((documento) => (
                <tr key={documento.id}>
                  <td>{documento.trabalhadorId}</td>
                  <td>{documento.tipoDocumentoId}</td>
                  <td>{documento.validoAte}</td>
                  <td>
                    <button
                      type="button"
                      disabled={ocupado}
                      onClick={() => selecionar(documento.id)}
                      aria-expanded={selecionado === documento.id}
                    >
                      {selecionado === documento.id ? 'Fechar' : 'Revisar'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selecionado &&
        estado.kind === 'success' &&
        estado.pagina.itens.some((item) => item.id === selecionado) && (
          <div className="aprovacoes-panel__review">
            <h3>Revisão do documento</h3>
            <p>Confira o arquivo antes de aprovar ou rejeitar.</p>
            <button type="button" onClick={() => void obterVisualizacao()} disabled={ocupado}>
              Gerar link de visualização
            </button>
            {urlVisualizacao && (
              <p>
                <a href={urlVisualizacao} target="_blank" rel="noopener noreferrer">
                  Abrir arquivo em outra aba (link temporário)
                </a>
              </p>
            )}
            {erroAcao && (
              <p role="alert" className="aprovacoes-panel__error">
                {erroAcao}
              </p>
            )}
            <div className="aprovacoes-panel__actions">
              <button type="button" onClick={() => void revisar('aprovacao')} disabled={ocupado}>
                {ocupado ? 'Processando...' : 'Confirmar aprovação'}
              </button>
              <form onSubmit={enviarRejeicao}>
                <label htmlFor="motivo-rejeicao">Motivo da rejeição (10 a 500 caracteres)</label>
                <textarea
                  id="motivo-rejeicao"
                  value={motivo}
                  onChange={(event) => setMotivo(event.target.value)}
                  minLength={10}
                  maxLength={500}
                  required
                  disabled={ocupado}
                  rows={3}
                />
                <button type="submit" disabled={ocupado || motivo.trim().length < 10}>
                  Rejeitar documento
                </button>
              </form>
            </div>
          </div>
        )}

      {estado.kind === 'success' && (
        <nav
          className="aprovacoes-panel__pagination"
          aria-label="Paginação de documentos pendentes"
        >
          <button
            type="button"
            disabled={indice === 0 || ocupado}
            onClick={() => {
              setSelecionado(null)
              setIndice((atual) => atual - 1)
            }}
          >
            Anterior
          </button>
          <span>Página {indice + 1}</span>
          <button
            type="button"
            disabled={!estado.pagina.proximoCursor || ocupado}
            onClick={() => {
              if (estado.pagina.proximoCursor) {
                setCursores((lista) => [...lista.slice(0, indice + 1), estado.pagina.proximoCursor])
                setSelecionado(null)
                setIndice((atual) => atual + 1)
              }
            }}
          >
            Próxima
          </button>
        </nav>
      )}
    </section>
  )
}
