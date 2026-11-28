import { getApps, initializeApp } from 'firebase/app'
import { connectAuthEmulator, getAuth } from 'firebase/auth'

// Apenas no desenvolvimento: configura o projeto fictício do Firebase Emulator Suite.
const usarEmulador = import.meta.env.DEV

const firebaseConfig = {
  apiKey: usarEmulador ? 'demo-habilita-local' : import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: usarEmulador
    ? 'demo-habilita.firebaseapp.com'
    : import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: usarEmulador ? 'demo-habilita' : import.meta.env.VITE_FIREBASE_PROJECT_ID,
}

// Sem configuração de produção, a tela informa a pendência em vez de quebrar o site.
export const firebaseConfigurado = Boolean(
  firebaseConfig.apiKey && firebaseConfig.authDomain && firebaseConfig.projectId,
)

const app = firebaseConfigurado ? (getApps()[0] ?? initializeApp(firebaseConfig)) : null
export const auth = app ? getAuth(app) : null

if (auth && usarEmulador && auth.emulatorConfig === null) {
  connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true })
}
