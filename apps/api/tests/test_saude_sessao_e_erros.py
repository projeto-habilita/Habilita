"""Testes de saúde, sessão (/me), paginação e tratamento de erros (RFC 9457).

Cobre:
- /healthz e /readyz respondendo sem login (HTTP 200)
- /me respondendo dados do usuário conforme claims (HTTP 200)
- /me respondendo HTTP 401 Problem Details sem header Authorization
- Extra fields rejeitados com HTTP 400 Problem Details (extra="forbid")
- Limite de paginação obedecido e refletido nas respostas
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from .conftest import TNT_ALFA


def test_healthz_e_readyz_sem_autenticacao(client: TestClient) -> None:
    """Rotas públicas de infraestrutura respondem 200 sem necessidade de login."""
    resp_health = client.get("/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json() == {"status": "ok"}
    assert "x-request-id" in resp_health.headers

    resp_ready = client.get("/readyz")
    assert resp_ready.status_code == 200
    assert resp_ready.json() == {"status": "ok"}
    assert "x-request-id" in resp_ready.headers


def test_me_usuario_autenticado_retorna_perfil(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """GET /me retorna dados do usuário autenticado a partir das custom claims."""
    resp = client.get("/me", headers=headers_admin_alfa)
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["tenantId"] == TNT_ALFA
    assert corpo["papel"] == "admin_contratante"
    assert "uid" in corpo


def test_me_sem_token_retorna_401_problem_details(client: TestClient) -> None:
    """Acesso sem token em rota protegida responde HTTP 401 Problem Details."""
    resp = client.get("/me")
    assert resp.status_code == 401
    assert "application/problem+json" in resp.headers.get("content-type", "")
    corpo = resp.json()
    assert corpo.get("status") == 401
    assert "title" in corpo


def test_corpo_invalido_com_campos_extras_retorna_400_problem_details(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """Campos extras são expressamente proibidos (extra='forbid') gerando HTTP 400."""
    payload_invalido = {
        "nome": "Tipo Válido",
        "obrigatorio": True,
        "campo_fantasma_malicioso": "proibido",
    }
    resp = client.post("/tipos-documento", json=payload_invalido, headers=headers_gestor_alfa)
    assert resp.status_code == 400
    assert "application/problem+json" in resp.headers.get("content-type", "")
    corpo = resp.json()
    assert corpo.get("status") == 400
    assert "extra inputs are not permitted" in str(corpo).lower()


def test_paginacao_limite_e_cursor(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """Verifica comportamento dos parâmetros de limite e estrutura de paginação."""
    resp = client.get("/terceirizadas?limite=1", headers=headers_gestor_alfa)
    assert resp.status_code == 200
    dados = resp.json()
    assert "itens" in dados
    assert len(dados["itens"]) <= 1
    assert "proximoCursor" in dados


def test_readyz_consulta_firestore_e_falha_quando_indisponivel(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida que /readyz consulta o Firestore de verdade e retorna 503 se falhar (BE-11)."""
    # 1. Sucesso quando a consulta ao Firestore funciona
    mock_db = MagicMock()
    mock_db.collection.return_value.limit.return_value.stream.return_value = []
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: mock_db)

    resp_sucesso = client.get("/readyz")
    assert resp_sucesso.status_code == 200
    assert resp_sucesso.json() == {"status": "ok"}
    mock_db.collection.assert_called_with("tenants")

    # 2. Falha 503 quando a consulta ao Firestore levanta erro
    mock_db_erro = MagicMock()
    mock_db_erro.collection.return_value.limit.return_value.stream.side_effect = RuntimeError(
        "Timeout de rede Firestore"
    )
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: mock_db_erro)

    resp_erro = client.get("/readyz")
    assert resp_erro.status_code == 503
    assert resp_erro.headers.get("content-type") == "application/problem+json"
    corpo = resp_erro.json()
    assert corpo.get("status") == 503
    assert corpo.get("title") == "Serviço indisponível"

    # 3. Fora de local sem cliente Firestore responde 503 imediatamente
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: None)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CPF_HMAC_KEY", "chave-secreta-producao-longa")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-de-teste-prod-nao-demo")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.habilita.com.br")

    resp_sem_banco_prod = client.get("/readyz")
    assert resp_sem_banco_prod.status_code == 503
