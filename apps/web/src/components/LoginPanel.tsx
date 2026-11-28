import { useEffect, useState, type FormEvent } from 'react'
import { FirebaseError } from 'firebase/app'
import {
  getIdTokenResult,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut,
  type User,
} from 'firebase/auth'
import { auth } from '../lib/firebase'
import { FormField } from './FormField'
import './LoginPanel.css'

export type Papel = 'admin_contratante' | 'analista_sst' | 'terceirizada' | 'porteiro'

export type SessaoAutenticada = {
  usuario: User
  email: string
  papel: Papel
  tenantId: string
}

type LoginPanelProps = {
  onSessionChange: (sessao: SessaoAutenticada | null) => void
}

function papelValido(papel: unknown): papel is Papel {
  return (
    papel === 'admin_contratante' ||
    papel === 'analista_sst' ||
    papel === 'terceirizada' ||
    papel === 'porteiro'
  )
}

async function obterSessao(usuario: User): Promise<SessaoAutenticada> {
  const resultado = await getIdTokenResult(usuario, true)
  const { papel, tenantId, terceirizadaId } = resultado.claims

  if (
    !papelValido(papel) ||
    typeof tenantId !== 'string' ||
    !tenantId.trim() ||
    (papel === 'terceirizada' && (typeof terceirizadaId !== 'string' || !terceirizadaId.trim()))
  ) {
    throw new Error('claims-ausentes')
  }

  return {
    usuario,
    email: usuario.email ?? 'Usuário autenticado',
    papel,
    tenantId,
  }
}

function mensagemAutenticacao(erro: unknown): string {
  if (erro instanceof FirebaseError) {
    switch (erro.code) {
      case 'auth/invalid-email':
        return 'Informe um e-mail válido.'
      case 'auth/invalid-credential':
      case 'auth/user-not-found':
      case 'auth/wrong-password':
        return 'E-mail ou senha inválidos.'
      case 'auth/user-disabled':
        return 'Esta conta está desativada.'
      case 'auth/network-request-failed':
        return 'Não foi possível conectar ao serviço de autenticação.'
      case 'auth/too-many-requests':
        return 'Muitas tentativas de acesso. Tente novamente mais tarde.'
      default:
        return 'Não foi possível autenticar. Tente novamente.'
    }
  }
  return 'Não foi possível concluir a autenticação.'
}

export function LoginPanel({ onSessionChange }: LoginPanelProps) {
  const [email, setEmail] = useState('')
  const [senha, setSenha] = useState('')
  const [sessao, setSessao] = useState<SessaoAutenticada | null>(null)
  const [verificando, setVerificando] = useState(true)
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')

  useEffect(() => {
    const instancia = auth
    if (!instancia) return

    let ativo = true
    const unsubscribe = onAuthStateChanged(
      instancia,
      (usuario) => {
        if (!ativo) return
        if (!usuario) {
          setSessao(null)
          onSessionChange(null)
          setVerificando(false)
          return
        }

        void (async () => {
          try {
            const proximaSessao = await obterSessao(usuario)
            if (!ativo) return
            setSessao(proximaSessao)
            onSessionChange(proximaSessao)
            setErro('')
          } catch {
            if (!ativo) return
            setSessao(null)
            onSessionChange(null)
            setErro('Conta sem permissões válidas no Habilita.')
            await signOut(instancia).catch(() => undefined)
          } finally {
            if (ativo) setVerificando(false)
          }
        })()
      },
      () => {
        if (!ativo) return
        setSessao(null)
        onSessionChange(null)
        setErro('Não foi possível restaurar a sessão.')
        setVerificando(false)
      },
    )

    return () => {
      ativo = false
      unsubscribe()
    }
  }, [onSessionChange])

  async function entrar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!auth) return
    setErro('')
    setEnviando(true)
    try {
      await signInWithEmailAndPassword(auth, email.trim(), senha)
      setSenha('')
    } catch (error) {
      setErro(mensagemAutenticacao(error))
    } finally {
      setEnviando(false)
    }
  }

  async function sair() {
    if (!auth) return
    setErro('')
    setEnviando(true)
    try {
      await signOut(auth)
      setSessao(null)
      onSessionChange(null)
    } catch (error) {
      setErro(mensagemAutenticacao(error))
    } finally {
      setEnviando(false)
    }
  }

  if (!auth) {
    return (
      <section aria-labelledby="login-title" className="login-panel">
        <h2 id="login-title">Acesso ao Habilita</h2>
        <p role="alert">
          Firebase Auth não configurado neste ambiente. É necessário definir as variáveis
          VITE_FIREBASE_API_KEY, VITE_FIREBASE_AUTH_DOMAIN e VITE_FIREBASE_PROJECT_ID no build.
        </p>
      </section>
    )
  }

  if (verificando) {
    return (
      <section aria-label="Autenticação" className="login-panel" aria-live="polite">
        <p>Verificando autenticação...</p>
      </section>
    )
  }

  if (sessao) {
    return (
      <section aria-labelledby="session-title" className="login-panel">
        <h2 id="session-title">Sessão autenticada</h2>
        <p role="status">Login realizado com sucesso.</p>
        <dl className="login-panel__details">
          <dt>Usuário</dt>
          <dd>{sessao.email}</dd>
          <dt>Perfil</dt>
          <dd>{sessao.papel}</dd>
        </dl>
        {erro && <p role="alert">{erro}</p>}
        <button type="button" onClick={() => void sair()} disabled={enviando}>
          {enviando ? 'Saindo...' : 'Sair'}
        </button>
      </section>
    )
  }

  return (
    <section aria-labelledby="login-title" className="login-panel">
      <h2 id="login-title">Acesso ao Habilita</h2>
      <p>Entre com as credenciais do seu perfil para acessar o sistema.</p>
      <form onSubmit={(event) => void entrar(event)}>
        <FormField
          id="login-email"
          name="email"
          type="email"
          label="E-mail"
          autoComplete="username"
          placeholder="Digite seu e-mail"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          disabled={enviando}
        />
        <FormField
          id="login-password"
          name="password"
          type="password"
          label="Senha"
          autoComplete="current-password"
          placeholder="Digite sua senha"
          value={senha}
          onChange={(event) => setSenha(event.target.value)}
          required
          disabled={enviando}
        />
        {erro && (
          <p role="alert" className="login-panel__error">
            {erro}
          </p>
        )}
        <button type="submit" disabled={enviando}>
          {enviando ? 'Entrando...' : 'Entrar'}
        </button>
      </form>
    </section>
  )
}
