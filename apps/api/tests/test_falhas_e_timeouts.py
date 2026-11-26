"""Categoria 4 — Testes de Falhas, Timeouts, Resiliência e Sanitização de Erros.

Cobre:
- Indisponibilidade, timeout e exceções do Firestore nos repositórios e em `/readyz` (-> 503).
- Falhas simuladas no adapter `SupabaseArmazenamento` (timeouts, erros de conexão,
  HTTP 500/502/403/404, JSON inválido em signed URL, redirecionamento inesperado).
- Payloads JSON malformados, CPFs inválidos, datas inválidas e Content-Type incorreto
  rejeitados sem vazar stack trace.
- Regressões rastreadas com `@pytest.mark.xfail(strict=True, reason="...")`:
  - `#112`: `criar_documento` engolindo `ArmazenamentoError` com `app_env == "local"` e
    gravando metadado órfão.
  - `#114`: `comum/auth.py` expondo mensagem crua de exceção Firebase no `detail` do HTTP 401.
"""

from __future__ import annotations

import datetime
import email.message
import io
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from habilita.armazenamento.porta import ArmazenamentoError, ObjetoNaoEncontradoError
from habilita.armazenamento.supabase import NoRedirectHandler, SupabaseArmazenamento
from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.cadastros.schemas import NovaTerceirizada
from habilita.comum.auth import extrair_usuario
from habilita.main import app
from habilita.trabalhadores.repositorio import TrabalhadoresRepositorio
from habilita.trabalhadores.schemas import ArquivoMetadados

from .conftest import (
    DOC_ALFA_1_ASO,
    TER_ALFA_1,
    TNT_ALFA,
    TRB_ALFA_1,
    mock_extrair_usuario,
)


def _configurar_ambiente_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CPF_HMAC_KEY", "chave-secreta-producao-para-teste-5678")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-service-prod-ficticia-nao-demo")
    monkeypatch.setenv("CORS_ORIGINS", "https://habilita.web.app")


def _assert_sem_stack_trace(texto: str) -> None:
    lower = texto.lower()
    assert "traceback" not in lower
    assert 'file "' not in lower
    assert '.py", line' not in lower


class _FakeHTTPResponse:
    """Resposta HTTP mínima em memória para simular retorno do urllib opener."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _http_error(url: str, code: int, body: bytes = b'{"error":"falha"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=url,
        code=code,
        msg="Erro simulado",
        hdrs=email.message.Message(),
        fp=io.BytesIO(body),
    )


# -----------------------------------------------------------------------------
# Firestore: indisponibilidade, timeout e exceções (/readyz -> 503 e repositórios)
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "erro_firestore",
    [
        TimeoutError("Deadline Exceeded ao consultar Cloud Firestore"),
        ConnectionError("Conexão recusada pelo emulador Firestore"),
        RuntimeError("503 Service Unavailable"),
    ],
    ids=["timeout", "connection_error", "runtime_503"],
)
def test_readyz_retorna_503_sem_stack_trace_quando_firestore_falha(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    erro_firestore: Exception,
) -> None:
    """/readyz deve responder HTTP 503 Problem Details se a consulta ao Firestore falhar."""
    mock_db = MagicMock()
    mock_db.collection.return_value.limit.return_value.stream.side_effect = erro_firestore
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: mock_db)

    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.headers.get("content-type", "").startswith("application/problem+json")
    corpo = resp.json()
    assert corpo["status"] == 503
    assert corpo["title"] == "Serviço indisponível"
    assert corpo["instance"] == "/readyz"
    _assert_sem_stack_trace(resp.text)


@pytest.mark.parametrize("env_nome", ["ci", "prod"])
def test_readyz_e_repositorios_falham_sem_firestore_fora_de_local(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    env_nome: str,
) -> None:
    """Fora de APP_ENV=local, ausência de Firestore retorna 503 e bloqueia fallback em memória."""
    _configurar_ambiente_prod(monkeypatch)
    monkeypatch.setenv("APP_ENV", env_nome)
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: None)
    monkeypatch.setattr("habilita.cadastros.repositorio.obter_firestore_client", lambda: None)
    monkeypatch.setattr("habilita.trabalhadores.repositorio.obter_firestore_client", lambda: None)

    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.headers.get("content-type", "").startswith("application/problem+json")

    with pytest.raises(RuntimeError, match="Armazenamento em memória proibido"):
        CadastrosRepositorio(tenant_id=TNT_ALFA)

    with pytest.raises(RuntimeError, match="Armazenamento em memória proibido"):
        TrabalhadoresRepositorio(tenant_id=TNT_ALFA)


def test_repositorios_propagam_timeout_e_excecao_do_firestore() -> None:
    """Timeout ou indisponibilidade do Firestore deve propagar sem corromper o estado."""
    mock_db = MagicMock()
    col_mock = MagicMock()
    col_mock.order_by.return_value.stream.side_effect = TimeoutError("Firestore deadline exceeded")
    col_mock.where.return_value.limit.return_value.stream.side_effect = ConnectionError(
        "Firestore unreachable"
    )
    mock_db.collection.return_value.document.return_value.collection.return_value = col_mock

    cad_repo = CadastrosRepositorio(tenant_id=TNT_ALFA, db=mock_db)
    with pytest.raises(TimeoutError):
        cad_repo.listar_terceirizadas()

    with pytest.raises(ConnectionError):
        cad_repo.criar_terceirizada(
            NovaTerceirizada(razaoSocial="Teste Timeout LTDA", cnpj="11222333000181")
        )

    trb_repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=mock_db)
    with pytest.raises(TimeoutError):
        trb_repo.listar_trabalhadores()


def test_http_nao_vaza_stack_trace_em_falha_inesperada_do_repositorio(
    headers_admin_alfa: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha não tratada no Firestore durante requisição HTTP retorna 500 sem expor traceback."""
    monkeypatch.setattr(
        "habilita.cadastros.repositorio.CadastrosRepositorio.listar_contratos",
        MagicMock(side_effect=TimeoutError("Erro interno simulado de socket Firestore")),
    )
    with TestClient(app, raise_server_exceptions=False) as sem_raise_client:
        resp = sem_raise_client.get("/contratos", headers=headers_admin_alfa)
    assert resp.status_code == 500
    _assert_sem_stack_trace(resp.text)
    assert "socket Firestore" not in resp.text


# -----------------------------------------------------------------------------
# SupabaseArmazenamento: timeouts, conexões, HTTP 500/502/403, JSON inválido e redirects
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "erro_transporte",
    [
        urllib.error.URLError(httpx.TimeoutException("Read timed out")),
        urllib.error.URLError(httpx.ConnectError("Connection refused")),
    ],
    ids=["httpx_timeout", "httpx_connect_error"],
)
def test_supabase_armazenamento_timeout_e_connect_error_viram_armazenamento_error(
    monkeypatch: pytest.MonkeyPatch,
    erro_transporte: Exception,
) -> None:
    """Simula timeout/erro de conexão em salvar, obter_url_leitura, excluir e saude."""
    storage = SupabaseArmazenamento(
        tenant_id=TNT_ALFA,
        base_url="http://storage.local:5000",
        service_key="chave-ficticia-teste",
        timeout_segundos=0.5,
    )
    monkeypatch.setattr(storage._opener, "open", MagicMock(side_effect=erro_transporte))

    with pytest.raises(ArmazenamentoError):
        storage.salvar(TRB_ALFA_1, DOC_ALFA_1_ASO, b"%PDF-1.4 ok", "pdf")

    with pytest.raises(ArmazenamentoError):
        storage.obter_url_leitura(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )

    with pytest.raises(ArmazenamentoError):
        storage.excluir(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )

    assert storage.saude() is False


@pytest.mark.parametrize("status_code", [500, 502, 403])
def test_supabase_armazenamento_http_500_502_403_levanta_armazenamento_error(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    """Respostas HTTP 500, 502 e 403 do Supabase Storage devem gerar ArmazenamentoError."""
    storage = SupabaseArmazenamento(
        tenant_id=TNT_ALFA,
        base_url="http://storage.local:5000",
        service_key="chave-ficticia-teste",
    )
    erro = _http_error("http://storage.local:5000/object/documentos/teste", status_code)
    monkeypatch.setattr(storage._opener, "open", MagicMock(side_effect=erro))

    with pytest.raises(ArmazenamentoError, match=f"HTTP {status_code}"):
        storage.salvar(TRB_ALFA_1, DOC_ALFA_1_ASO, b"%PDF-1.4 ok", "pdf")

    with pytest.raises(ArmazenamentoError, match=f"HTTP {status_code}"):
        storage.obter_url_leitura(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )

    with pytest.raises(ArmazenamentoError, match=f"HTTP {status_code}"):
        storage.excluir(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )

    assert storage.saude() is False


@pytest.mark.parametrize(
    "corpo_invalido",
    [
        b"<html><body>Bad Gateway</body></html>",
        b"{json_truncado",
        b'{"outroCampo": "/signed/123"}',
        b'{"signedURL": ""}',
    ],
    ids=["html", "json_truncado", "sem_signedURL", "signedURL_vazia"],
)
def test_supabase_armazenamento_json_invalido_em_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    corpo_invalido: bytes,
) -> None:
    """Resposta 200 com JSON inválido ou sem `signedURL` deve levantar ArmazenamentoError."""
    storage = SupabaseArmazenamento(
        tenant_id=TNT_ALFA,
        base_url="http://storage.local:5000",
        service_key="chave-ficticia-teste",
    )
    monkeypatch.setattr(
        storage._opener,
        "open",
        MagicMock(return_value=_FakeHTTPResponse(200, corpo_invalido)),
    )

    with pytest.raises(ArmazenamentoError):
        storage.obter_url_leitura(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )


def test_supabase_armazenamento_bloqueia_redirecionamento_inesperado() -> None:
    """NoRedirectHandler impede redirecionamento e converte 302/307 em ArmazenamentoError."""
    handler = NoRedirectHandler()
    req = urllib.request.Request("http://storage.local:5000/object/sign/documentos/teste.pdf")  # noqa: S310
    assert (
        handler.redirect_request(
            req,
            io.BytesIO(b""),
            302,
            "Found",
            email.message.Message(),
            "https://evil.example.com/steal-key",
        )
        is None
    )

    storage = SupabaseArmazenamento(
        tenant_id=TNT_ALFA,
        base_url="http://storage.local:5000",
        service_key="segredo-service-role",
    )
    with pytest.raises(ArmazenamentoError, match="HTTP 307"):
        # OpenerDirector real dispara HTTPError 307 quando NoRedirectHandler retorna None
        def _simular_redirect(*_: object, **__: object) -> Any:
            return storage._opener.error(
                "http",
                req,
                io.BytesIO(b""),
                307,
                "Temporary Redirect",
                email.message.Message(),
            )

        storage._opener.open = _simular_redirect  # type: ignore[method-assign]
        storage.obter_url_leitura(
            trabalhador_id=TRB_ALFA_1,
            documento_id=DOC_ALFA_1_ASO,
            nome_arquivo="doc.pdf",
        )


def test_gerar_url_download_em_prod_propaga_erro_storage_e_retorna_none_em_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fora de APP_ENV=local, falha de Storage propaga e 404 do Storage vira None (HTTP 404)."""
    _configurar_ambiente_prod(monkeypatch)
    mock_db = MagicMock()
    snap = MagicMock()
    snap.exists = True
    snap.to_dict.return_value = {
        "id": DOC_ALFA_1_ASO,
        "trabalhadorId": TRB_ALFA_1,
        "tipoDocumentoId": "tip_aso",
        "validoAte": "2027-01-01",
        "statusRevisao": "APROVADO",
        "enviadoEm": "2026-10-15T10:00:00Z",
        "arquivo": {
            "contentType": "application/pdf",
            "tamanhoBytes": 120,
            "sha256": "a" * 64,
        },
    }
    mock_doc_ref = (
        mock_db.collection.return_value.document.return_value.collection.return_value.document
    )
    mock_doc_ref.return_value.get.return_value = snap

    storage_erro = MagicMock()
    storage_erro.obter_url_leitura.side_effect = ArmazenamentoError("Storage timeout")
    repo_erro = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=mock_db, storage=storage_erro)
    with pytest.raises(ArmazenamentoError):
        repo_erro.gerar_url_download(DOC_ALFA_1_ASO)

    storage_404 = MagicMock()
    storage_404.obter_url_leitura.side_effect = ObjetoNaoEncontradoError("Ausente")
    repo_404 = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=mock_db, storage=storage_404)
    assert repo_404.gerar_url_download(DOC_ALFA_1_ASO) is None


# -----------------------------------------------------------------------------
# Payloads malformados, CPFs inválidos, datas inválidas e Content-Type incorreto
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rota", "payload_bruto"),
    [
        ("/terceirizadas", b"{json invalido"),
        ("/contratos", b""),
        ("/trabalhadores", b'{"nome": "Teste", '),
        (f"/documentos/{DOC_ALFA_1_ASO}/rejeicao", b"\xff\xfe\x00"),
    ],
    ids=[
        "terceirizadas_json_quebrado",
        "contratos_vazio",
        "trabalhadores_truncado",
        "rejeicao_binario",
    ],
)
def test_payload_json_malformado_retorna_400_sem_stack_trace(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    rota: str,
    payload_bruto: bytes,
) -> None:
    """JSON malformado deve retornar 400 Problem Details sem vazar stack trace."""
    resp = client.post(
        rota,
        content=payload_bruto,
        headers={**headers_admin_alfa, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.headers.get("content-type", "").startswith("application/problem+json")
    _assert_sem_stack_trace(resp.text)


@pytest.mark.parametrize(
    "cpf_invalido",
    [
        "00000000000",
        "111.111.111-11",
        "123.456.789-00",
        "1234567890",
        "abcdefghijk",
        "' OR 1=1 --",
    ],
)
def test_cpfs_invalidos_rejeitados_com_400_sem_stack_trace(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    cpf_invalido: str,
) -> None:
    """CPFs inválidos ou maliciosos são rejeitados com 400 Problem Details sem stack trace."""
    resp = client.post(
        "/trabalhadores",
        json={"nome": "Nome Fictício Teste", "cpf": cpf_invalido, "terceirizadaId": TER_ALFA_1},
        headers=headers_admin_alfa,
    )
    assert resp.status_code == 400
    assert resp.headers.get("content-type", "").startswith("application/problem+json")
    _assert_sem_stack_trace(resp.text)


def test_datas_invalidas_e_inconsistentes_retornam_400_sem_stack_trace(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """Datas impossíveis, formato inválido ou ordem invertida retornam 400 sem vazar stack trace."""
    # Contrato com formato inválido e período invertido
    for inicio, fim in [("31/12/2026", "2027-01-01"), ("2027-05-01", "2026-05-01")]:
        resp_ctr = client.post(
            "/contratos",
            json={
                "identificador": "CTR-DATA-INV",
                "terceirizadaId": TER_ALFA_1,
                "dataInicio": inicio,
                "dataFim": fim,
            },
            headers=headers_admin_alfa,
        )
        assert resp_ctr.status_code == 400
        assert resp_ctr.headers.get("content-type", "").startswith("application/problem+json")
        _assert_sem_stack_trace(resp_ctr.text)

    # Upload com data inexistente, formato não ISO, emissão futura e validade < emissão
    futuro = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    casos_upload = [
        ("2026-02-30", None),
        ("31/12/2027", None),
        ("2028-01-01", futuro),
        ("2026-01-01", "2026-06-01"),
    ]
    for valido_ate, data_emissao in casos_upload:
        dados = {"tipoDocumentoId": "tip_aso", "validoAte": valido_ate}
        if data_emissao is not None:
            dados["dataEmissao"] = data_emissao
        resp_up = client.post(
            f"/trabalhadores/{TRB_ALFA_1}/documentos",
            files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 ok"), "application/pdf")},
            data=dados,
            headers=headers_terceirizada_1_alfa,
        )
        assert resp_up.status_code == 400
        assert resp_up.headers.get("content-type", "").startswith("application/problem+json")
        _assert_sem_stack_trace(resp_up.text)

    # Aprovação com data inválida
    resp_up_ok = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 ok"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up_ok.status_code == 201
    doc_id = resp_up_ok.json()["id"]

    resp_aprov = client.post(
        f"/documentos/{doc_id}/aprovacao",
        json={"validoAte": "2026-02-30"},
        headers=headers_analista_alfa,
    )
    assert resp_aprov.status_code == 400
    assert resp_aprov.headers.get("content-type", "").startswith("application/problem+json")
    _assert_sem_stack_trace(resp_aprov.text)


def test_content_type_incorreto_rejeitado_sem_stack_trace(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """Content-Type incompatível ou bytes falsos retorna 400/415 sem vazar stack trace."""
    resp_txt = client.post(
        "/contratos",
        content=b"identificador=CTR-1",
        headers={**headers_admin_alfa, "Content-Type": "text/plain"},
    )
    assert resp_txt.status_code == 400
    assert resp_txt.headers.get("content-type", "").startswith("application/problem+json")
    _assert_sem_stack_trace(resp_txt.text)

    resp_json_multipart = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        json={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_json_multipart.status_code == 400
    content_type_multi = resp_json_multipart.headers.get("content-type", "")
    assert content_type_multi.startswith("application/problem+json")
    _assert_sem_stack_trace(resp_json_multipart.text)

    resp_magic = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("falso.pdf", io.BytesIO(b"<html>script</html>"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_magic.status_code == 415
    assert resp_magic.headers.get("content-type", "").startswith("application/problem+json")
    _assert_sem_stack_trace(resp_magic.text)


# -----------------------------------------------------------------------------
# Regressões rastreadas para defeitos abertos #112 e #114
# -----------------------------------------------------------------------------
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Defeito #112: TrabalhadoresRepositorio.criar_documento engole ArmazenamentoError "
        "quando app_env == 'local' e grava metadado órfão no Firestore/memória."
    ),
)
def test_regressao_112_criar_documento_nao_engole_armazenamento_error_nem_grava_orfao() -> None:
    """Falha no Storage ao salvar arquivo deve abortar sem persistir metadado órfão (#112)."""
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_storage = MagicMock()
    mock_storage.salvar.side_effect = ArmazenamentoError("Timeout no Supabase Storage")

    repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=mock_db, storage=mock_storage)
    meta = ArquivoMetadados(contentType="application/pdf", tamanhoBytes=12, sha256="a" * 64)

    with pytest.raises(ArmazenamentoError):
        repo.criar_documento(
            trabalhador_id=TRB_ALFA_1,
            tipo_documento_id="tip_aso",
            valido_ate="2027-12-31",
            data_emissao="2026-01-01",
            arquivo_meta=meta,
            conteudo=b"%PDF-1.4 ok",
        )

    mock_tenant_doc.collection.return_value.document.return_value.set.assert_not_called()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Defeito #114: comum/auth.py expõe mensagem crua da exceção do Firebase Admin SDK "
        "no campo 'detail' do HTTP 401 Problem Details."
    ),
)
def test_regressao_114_auth_401_nao_expoe_mensagem_crua_de_excecao_firebase(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erro interno do Firebase Auth não deve vazar no `detail` do HTTP 401 (#114)."""
    segredo_interno = "firebase-admin-sdk socket timeout em 10.240.0.12:443"

    def _falhar_verify(_token: str) -> dict[str, Any]:
        raise RuntimeError(segredo_interno)

    monkeypatch.setattr("firebase_admin.auth.verify_id_token", _falhar_verify)
    app.dependency_overrides.pop(extrair_usuario, None)
    try:
        resp = client.get("/me", headers={"Authorization": "Bearer token-invalido-qualquer"})
    finally:
        app.dependency_overrides[extrair_usuario] = mock_extrair_usuario

    assert resp.status_code == 401
    assert resp.headers.get("content-type", "").startswith("application/problem+json")
    detail = str(resp.json().get("detail", ""))
    assert "10.240.0.12" not in detail
    assert "firebase-admin-sdk" not in detail
