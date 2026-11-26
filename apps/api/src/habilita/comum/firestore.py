"""Gerenciamento de conexão com o Cloud Firestore.

Compatível com o Firebase Emulator Suite local e produção GCP.
Conforme AGENTS.md e ADR 0002/0010:
- Conecta aos emuladores se FIRESTORE_EMULATOR_HOST estiver presente.
- Utiliza credenciais anônimas em desenvolvimento local.
- Fallback gracioso para modo de testes rápidos se o banco não estiver disponível.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import firebase_admin  # type: ignore[import-untyped]
import google.auth.credentials
from firebase_admin import credentials, firestore
from google.cloud.firestore import Client as FirestoreClient

from habilita.config import get_settings

logger = logging.getLogger(__name__)

_firestore_client: FirestoreClient | None = None
_inicializacao_tentada: bool = False


class EmulatorCredentials(credentials.Base):  # type: ignore[misc]
    """Credencial anônima para conexão exclusiva com emuladores locais."""

    def get_credential(self) -> google.auth.credentials.Credentials:
        return google.auth.credentials.AnonymousCredentials()  # type: ignore[no-untyped-call]


def _carregar_credencial_service_account(raw_secret: str) -> credentials.Certificate | None:
    """Decodifica credencial de conta de serviço do Firebase a partir de JSON puro ou Base64."""
    texto = raw_secret.strip()
    if not texto:
        return None
    if not texto.startswith("{"):
        texto = base64.b64decode(texto).decode("utf-8")
    dados = json.loads(texto)
    return credentials.Certificate(dados)


def obter_firebase_app() -> firebase_admin.App:
    """Inicializa ou retorna a aplicação Firebase Admin padrão."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        settings = get_settings()
        if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
            return firebase_admin.initialize_app(
                credential=EmulatorCredentials(),
                options={"projectId": settings.firebase_project_id},
            )
        cred = _carregar_credencial_service_account(
            settings.firebase_service_account_json.get_secret_value()
        )
        if cred is not None:
            return firebase_admin.initialize_app(
                credential=cred,
                options={"projectId": settings.firebase_project_id},
            )
        return firebase_admin.initialize_app(
            options={"projectId": settings.firebase_project_id},
        )


def obter_firestore_client() -> FirestoreClient | None:
    """Retorna a instância do cliente Firestore ou None se emuladores/GCP indisponíveis."""
    global _firestore_client, _inicializacao_tentada

    if _firestore_client is not None:
        return _firestore_client

    if _inicializacao_tentada and _firestore_client is None:
        return None

    _inicializacao_tentada = True

    settings = get_settings()

    # Se estamos sem emuladores e sem credenciais, não inicializa cliente em ambiente local
    emulator_host = os.getenv("FIRESTORE_EMULATOR_HOST")
    google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    if not emulator_host and not google_creds and settings.app_env == "local":
        logger.debug("FIRESTORE_EMULATOR_HOST não definido. Operando em modo de memória local.")
        return None

    try:
        app = obter_firebase_app()
        client: FirestoreClient = firestore.client(app=app)
        _firestore_client = client
        logger.info("Cliente Firestore inicializado com sucesso (emulator=%s).", emulator_host)
        return _firestore_client
    except Exception as exc:
        if settings.app_env != "local":
            logger.error("Falha ao conectar ao Cloud Firestore fora do ambiente local (%s).", exc)
            return None
        logger.warning(
            "Não foi possível conectar ao Firestore (%s). Usando fallback em memória.", exc
        )
        return None


def resetar_cliente_firestore_para_testes() -> None:
    """Permite redefinir o cliente durante testes."""
    global _firestore_client, _inicializacao_tentada
    _firestore_client = None
    _inicializacao_tentada = False
