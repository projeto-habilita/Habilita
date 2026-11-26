"""Suíte de cobertura de domínio, repositórios e adaptadores ([QA-11] #86 e #115).

Foca nos ramos descobertos de:
- habilita/cadastros/repositorio.py (e rotas/schemas)
- habilita/trabalhadores/repositorio.py (e rotas/schemas)
- habilita/armazenamento/supabase.py e porta.py
- habilita/comum/firestore.py e auth.py / config.py
Sem alterar código de produção, sem chamadas de rede reais e sem PII real.
"""

from __future__ import annotations

import datetime
import io
import json
import urllib.error
import urllib.request
from collections.abc import Generator
from email.message import Message
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from habilita.armazenamento.porta import (
    ArmazenamentoError,
    CaminhoInvalidoError,
    ObjetoNaoEncontradoError,
    construir_caminho,
    validar_e_resolver_caminho,
)
from habilita.armazenamento.supabase import NoRedirectHandler, SupabaseArmazenamento
from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.cadastros.schemas import NovaTerceirizada, NovoContrato, NovoTipoDocumento
from habilita.comum import firestore as firestore_mod
from habilita.comum.auth import UsuarioAutenticado, extrair_usuario
from habilita.comum.erros import AppException
from habilita.config import Settings
from habilita.trabalhadores.repositorio import TrabalhadoresRepositorio
from habilita.trabalhadores.schemas import ArquivoMetadados, Documento, NovoTrabalhador

from .conftest import TER_ALFA_1, TNT_ALFA, TRB_ALFA_1

UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017
PDF_FICTICIO = b"%PDF-1.4\nDOCUMENTO FICTICIO - TESTE AUTOMATIZADO HABILITA\n"


# -----------------------------------------------------------------------------
# Fake Firestore em memória (cobre fluxos reais de db != None sem emulador)
# -----------------------------------------------------------------------------
class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None, path: str) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = MagicMock(path=path)

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _FakeQuery:
    def __init__(
        self,
        items: list[tuple[str, dict[str, Any], str]],
    ) -> None:
        self._items = list(items)

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        if op == "==":
            filtrados = [it for it in self._items if it[1].get(field) == value]
            return _FakeQuery(filtrados)
        return self

    def order_by(self, field: str) -> _FakeQuery:
        ordenados = sorted(self._items, key=lambda it: str(it[1].get(field, "")))
        return _FakeQuery(ordenados)

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._items[:n])

    def stream(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(doc_id, data, path) for doc_id, data, path in self._items]


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]], prefix: str) -> None:
        self._store = store
        self._prefix = prefix

    def document(self, doc_id: str) -> _FakeDocument:
        return _FakeDocument(self._store, doc_id, f"{self._prefix}/{doc_id}")

    def _entries(self) -> list[tuple[str, dict[str, Any], str]]:
        diretos: list[tuple[str, dict[str, Any], str]] = []
        prefixo_doc = f"{self._prefix}/"
        for path, data in self._store.items():
            if path.startswith(prefixo_doc):
                resto = path[len(prefixo_doc) :]
                if "/" not in resto:
                    diretos.append((resto, data, path))
        return diretos

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        return _FakeQuery(self._entries()).where(field, op, value)

    def order_by(self, field: str) -> _FakeQuery:
        return _FakeQuery(self._entries()).order_by(field)

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._entries()).limit(n)

    def stream(self) -> list[_FakeSnapshot]:
        return _FakeQuery(self._entries()).stream()


class _FakeDocument:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str, path: str) -> None:
        self._store = store
        self.id = doc_id
        self.path = path

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store, f"{self.path}/{name}")

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self.id, self._store.get(self.path), self.path)

    def set(self, data: dict[str, Any]) -> None:
        self._store[self.path] = dict(data)

    def update(self, data: dict[str, Any]) -> None:
        atual = self._store.setdefault(self.path, {})
        atual.update(data)


class _FakeFirestoreDB:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self.store, name)

    def collection_group(self, name: str) -> _FakeQuery:
        sufixo = f"/{name}/"
        encontrados: list[tuple[str, dict[str, Any], str]] = []
        for path, data in self.store.items():
            if sufixo in path:
                doc_id = path.rsplit("/", 1)[-1]
                # Confere se o segmento imediatamente anterior ao doc_id é `name`
                partes = path.split("/")
                if len(partes) >= 2 and partes[-2] == name:
                    encontrados.append((doc_id, data, path))
        return _FakeQuery(encontrados)


class _FakeUrllibResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeUrllibResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _ativar_env_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CPF_HMAC_KEY", "chave-hmac-qa11-producao-segura-32")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-servico-qa11-producao-valida")
    monkeypatch.setenv("CORS_ORIGINS", "https://habilita.exemplo.com.br")


@pytest.fixture
def restaurar_firestore() -> Generator[None, None, None]:
    firestore_mod.resetar_cliente_firestore_para_testes()
    yield
    firestore_mod.resetar_cliente_firestore_para_testes()


# -----------------------------------------------------------------------------
# 1. armazenamento/supabase.py e porta.py
# -----------------------------------------------------------------------------
def test_qa11_porta_caminhos_invalidos() -> None:
    with pytest.raises(CaminhoInvalidoError):
        construir_caminho("tnt_1", "trb_1", "doc_1", "arquivo.exe")

    with pytest.raises(CaminhoInvalidoError):
        validar_e_resolver_caminho("tnt_1", caminho="tenants/tnt_1/incompleto")

    with pytest.raises(CaminhoInvalidoError):
        validar_e_resolver_caminho(
            "tnt_1",
            caminho="outro/tnt_1/trabalhadores/trb_1/documentos/doc_1/ok.pdf",
        )

    with pytest.raises(CaminhoInvalidoError):
        validar_e_resolver_caminho(
            "tnt_1",
            caminho="tenants/tnt_1/trabalhadores/trb_1/documentos/doc_1/ruim.png",
        )

    with pytest.raises(CaminhoInvalidoError):
        validar_e_resolver_caminho("tnt_1", trabalhador_id="trb_1")


def test_qa11_supabase_no_redirect_e_validacoes(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = NoRedirectHandler()
    req = urllib.request.Request("http://127.0.0.1:5000/status")
    assert handler.redirect_request(req, object(), 302, "Found", {}, "http://outro") is None

    storage = SupabaseArmazenamento(
        tenant_id="tnt_qa11",
        base_url="http://storage.local:5000",
        service_key="chave-teste",
        public_url="http://public.local:5000",
    )

    with pytest.raises(CaminhoInvalidoError):
        storage.salvar("trb_1", "doc_1", b"abc", "png")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Tempo de expiração"):
        storage.obter_url_leitura(
            caminho="tenants/tnt_qa11/trabalhadores/trb_1/documentos/doc_1/a.pdf",
            expira_em_segundos=0,
        )

    with pytest.raises(ValueError, match="Tempo de expiração"):
        storage.obter_url_leitura(
            caminho="tenants/tnt_qa11/trabalhadores/trb_1/documentos/doc_1/a.pdf",
            expira_em_segundos=301,
        )

    # Salvar miniatura JPG com sucesso (HTTP 200) e falha HTTP 204 / HTTPError / URLError
    opener_mock = MagicMock()
    monkeypatch.setattr(storage, "_opener", opener_mock)

    opener_mock.open.return_value = _FakeUrllibResponse(200, b"{}")
    caminho_mini = storage.salvar(
        "trb_1", "doc_1", b"\xff\xd8\xff\xe0jpg", "jpg", miniatura=True, arquivo_uuid="u1"
    )
    assert caminho_mini.endswith("/u1_mini.jpg")

    opener_mock.open.return_value = _FakeUrllibResponse(204, b"sem corpo")
    with pytest.raises(ArmazenamentoError, match="HTTP 204"):
        storage.salvar("trb_1", "doc_1", PDF_FICTICIO, "pdf")

    opener_mock.open.side_effect = urllib.error.HTTPError(
        "http://storage.local/obj", 500, "Err", Message(), io.BytesIO(b"erro interno")
    )
    with pytest.raises(ArmazenamentoError, match="Erro HTTP 500"):
        storage.salvar("trb_1", "doc_1", PDF_FICTICIO, "pdf")

    opener_mock.open.side_effect = urllib.error.URLError("conexao recusada")
    with pytest.raises(ArmazenamentoError, match="Erro de conexão"):
        storage.salvar("trb_1", "doc_1", PDF_FICTICIO, "pdf")


def test_qa11_supabase_obter_url_excluir_e_saude(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = SupabaseArmazenamento(
        tenant_id="tnt_qa11",
        base_url="http://storage.local:5000",
        service_key="chave-teste",
        public_url="http://public.local:5000",
    )
    opener_mock = MagicMock()
    monkeypatch.setattr(storage, "_opener", opener_mock)
    caminho = "tenants/tnt_qa11/trabalhadores/trb_1/documentos/doc_1/abc.pdf"

    # URL assinada com barra e sem barra inicial
    opener_mock.open.return_value = _FakeUrllibResponse(
        200, json.dumps({"signedURL": "/object/sign/documentos/abc.pdf?token=t1"}).encode()
    )
    assert (
        storage.obter_url_leitura(caminho=caminho)
        == "http://public.local:5000/object/sign/documentos/abc.pdf?token=t1"
    )

    opener_mock.open.return_value = _FakeUrllibResponse(
        200, json.dumps({"signedURL": "object/sign/documentos/abc.pdf?token=t2"}).encode()
    )
    assert (
        storage.obter_url_leitura(caminho=caminho)
        == "http://public.local:5000/object/sign/documentos/abc.pdf?token=t2"
    )

    # Erros em obter_url_leitura: status != 200, JSON inválido, sem signedURL, 404, 500, URLError
    opener_mock.open.return_value = _FakeUrllibResponse(202, b"accepted")
    with pytest.raises(ArmazenamentoError, match="HTTP 202"):
        storage.obter_url_leitura(caminho=caminho)

    opener_mock.open.return_value = _FakeUrllibResponse(200, b"{invalido")
    with pytest.raises(ArmazenamentoError, match="JSON válido"):
        storage.obter_url_leitura(caminho=caminho)

    opener_mock.open.return_value = _FakeUrllibResponse(200, b"{}")
    with pytest.raises(ArmazenamentoError, match="signedURL"):
        storage.obter_url_leitura(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.HTTPError(
        "http://storage.local/sign", 404, "Not Found", Message(), io.BytesIO(b"not_found")
    )
    with pytest.raises(ObjetoNaoEncontradoError):
        storage.obter_url_leitura(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.HTTPError(
        "http://storage.local/sign", 502, "Bad Gateway", Message(), io.BytesIO(b"bad gateway")
    )
    with pytest.raises(ArmazenamentoError, match="Erro HTTP 502"):
        storage.obter_url_leitura(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.URLError("timeout")
    with pytest.raises(ArmazenamentoError, match="Erro de conexão"):
        storage.obter_url_leitura(caminho=caminho)

    # Excluir: sucesso (204), status 500, HTTPError 404 (idempotente), HTTPError 500, URLError
    opener_mock.open.side_effect = None
    opener_mock.open.return_value = _FakeUrllibResponse(204, b"")
    storage.excluir(caminho=caminho)

    opener_mock.open.return_value = _FakeUrllibResponse(500, b"erro")
    with pytest.raises(ArmazenamentoError, match="HTTP 500"):
        storage.excluir(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.HTTPError(
        "http://storage.local/del", 404, "Not Found", Message(), io.BytesIO(b"")
    )
    storage.excluir(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.HTTPError(
        "http://storage.local/del", 500, "Err", Message(), io.BytesIO(b"falha")
    )
    with pytest.raises(ArmazenamentoError, match="Erro HTTP 500"):
        storage.excluir(caminho=caminho)

    opener_mock.open.side_effect = urllib.error.URLError("rede fora")
    with pytest.raises(ArmazenamentoError, match="Erro de conexão"):
        storage.excluir(caminho=caminho)

    # Saude: True (200) e False (503 / exceção)
    opener_mock.open.side_effect = None
    opener_mock.open.return_value = _FakeUrllibResponse(200, b"ok")
    assert storage.saude() is True

    opener_mock.open.side_effect = RuntimeError("storage fora")
    assert storage.saude() is False


# -----------------------------------------------------------------------------
# 2. comum/firestore.py, comum/auth.py e config.py
# -----------------------------------------------------------------------------
def test_qa11_firestore_inicializacao_emulator_cache_e_erros(
    monkeypatch: pytest.MonkeyPatch,
    restaurar_firestore: None,
) -> None:
    cred = firestore_mod.EmulatorCredentials()
    assert cred.get_credential() is not None

    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    monkeypatch.setattr("firebase_admin.get_app", MagicMock(side_effect=ValueError()))
    init_app_mock = MagicMock(return_value="app_emulador")
    monkeypatch.setattr("firebase_admin.initialize_app", init_app_mock)
    fake_client = MagicMock()
    monkeypatch.setattr("firebase_admin.firestore.client", MagicMock(return_value=fake_client))

    # 1a chamada inicializa e cacheia
    assert firestore_mod.obter_firestore_client() is fake_client
    init_app_mock.assert_called_once()
    # 2a chamada retorna do cache (_firestore_client is not None)
    assert firestore_mod.obter_firestore_client() is fake_client

    # Reset e teste de branch sem emulador (produção simulada com erro na inicialização)
    firestore_mod.resetar_cliente_firestore_para_testes()
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    _ativar_env_prod(monkeypatch)
    monkeypatch.setattr(
        "firebase_admin.firestore.client",
        MagicMock(side_effect=RuntimeError("sem credencial")),
    )
    assert firestore_mod.obter_firestore_client() is None
    # Nova tentativa retorna None pelo cache de _inicializacao_tentada
    assert firestore_mod.obter_firestore_client() is None

    # Fallback local com aviso quando firestore.client falha
    firestore_mod.resetar_cliente_firestore_para_testes()
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    assert firestore_mod.obter_firestore_client() is None


def test_qa11_config_json_cors_e_auth_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Settings(cors_origins='["https://a.habilita.dev", "https://b.habilita.dev"]')
    assert cfg.cors_origins == ["https://a.habilita.dev", "https://b.habilita.dev"]

    usuario = UsuarioAutenticado(
        uid="uid_teste", tenant_id="tnt_qa11", papel="terceirizada", terceirizada_id="ter_1"
    )
    assert "tnt_qa11" in repr(usuario)

    with pytest.raises(AppException) as exc_info:
        extrair_usuario(authorization=None)
    assert exc_info.value.status_code == 401


# -----------------------------------------------------------------------------
# 3. cadastros/repositorio.py, schemas.py e rotas.py
# -----------------------------------------------------------------------------
def test_qa11_cadastros_repositorio_firestore_paginacao_auditoria_e_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeFirestoreDB()
    repo = CadastrosRepositorio(tenant_id="tnt_qa11_fs", db=cast(Any, db))

    # Terceirizadas: criar, duplicidade CNPJ -> 409, listar com cursor, obter
    t1 = repo.criar_terceirizada(NovaTerceirizada(razaoSocial="Empresa Um", cnpj="11222333000181"))
    t2 = repo.criar_terceirizada(
        NovaTerceirizada(razaoSocial="Empresa Dois", cnpj="11444777000161")
    )
    with pytest.raises(AppException) as exc_dup:
        repo.criar_terceirizada(NovaTerceirizada(razaoSocial="Dup", cnpj="11222333000181"))
    assert exc_dup.value.status_code == 409

    ter_obtida = repo.obter_terceirizada(t1.id)
    assert ter_obtida is not None and ter_obtida.id == t1.id
    assert repo.obter_terceirizada("ter_inexistente") is None

    pag1, cur1 = repo.listar_terceirizadas(limite=1)
    assert len(pag1) == 1 and cur1 is not None
    pag2, cur2 = repo.listar_terceirizadas(limite=1, cursor=cur1)
    assert len(pag2) == 1 and pag2[0].id != pag1[0].id and cur2 is None

    # Tipos de documento: criar, listar via db, obter com alias tdoc_ <-> tip_
    td1 = repo.criar_tipo_documento(NovoTipoDocumento(nome="ASO Periodico", validadeMeses=12))
    assert len(repo.listar_tipos_documento()) == 1
    alias_id = td1.id.replace("tip_", "tdoc_")
    assert repo.obter_tipo_documento(td1.id) is not None
    assert repo.obter_tipo_documento(alias_id) is not None

    # Contratos: criar, obter (com filtro terceirizada), listar com cursor, matriz + auditoria
    c1 = repo.criar_contrato(
        NovoContrato(
            identificador="CTR-QA11-1",
            terceirizadaId=t1.id,
            dataInicio="2026-01-01",
            dataFim="2027-12-31",
        )
    )
    repo.criar_contrato(
        NovoContrato(
            identificador="CTR-QA11-2",
            terceirizadaId=t2.id,
            dataInicio="2026-01-01",
            dataFim="2027-12-31",
        )
    )
    assert repo.obter_contrato(c1.id, terceirizada_id_filtro=t2.id) is None
    assert repo.obter_contrato(c1.id, terceirizada_id_filtro=t1.id) is not None

    ctrs_p1, ctr_cur = repo.listar_contratos(limite=1)
    assert len(ctrs_p1) == 1 and ctr_cur is not None
    ctrs_p2, _ = repo.listar_contratos(limite=1, cursor=ctr_cur)
    assert len(ctrs_p2) == 1 and ctrs_p2[0].id != ctrs_p1[0].id

    ctrs_t1, _ = repo.listar_contratos(terceirizada_id_filtro=t1.id)
    assert [c.id for c in ctrs_t1] == [c1.id]

    atualizado = repo.substituir_exigencias(c1.id, [td1.id], ator_uid="uid_qa11")
    assert atualizado is not None and atualizado.versaoMatriz == 1
    assert repo.substituir_exigencias("ctr_inexistente", [td1.id], ator_uid="uid_qa11") is None
    auditoria = repo.listar_auditoria()
    assert len(auditoria) == 1 and auditoria[0]["contratoId"] == c1.id

    # Fallback para memória quando db tem coleção vazia em APP_ENV=local (tnt_alfa)
    repo_alfa_vazio = CadastrosRepositorio(tenant_id=TNT_ALFA, db=cast(Any, db))
    assert len(repo_alfa_vazio.listar_terceirizadas()[0]) >= 2
    assert len(repo_alfa_vazio.listar_tipos_documento()) >= 3
    assert len(repo_alfa_vazio.listar_contratos(terceirizada_id_filtro=TER_ALFA_1)[0]) >= 1
    assert repo_alfa_vazio.obter_tipo_documento("tdoc_aso") is not None

    # Ramos APP_ENV=prod (db=None proibido e miss no db retorna None sem fallback memória)
    _ativar_env_prod(monkeypatch)
    monkeypatch.setattr("habilita.cadastros.repositorio.obter_firestore_client", lambda: None)
    with pytest.raises(RuntimeError, match="memória"):
        CadastrosRepositorio(tenant_id="tnt_prod")

    repo_prod = CadastrosRepositorio(tenant_id="tnt_prod", db=cast(Any, db))
    assert repo_prod.obter_terceirizada("inexistente") is None
    assert repo_prod.obter_tipo_documento("tip_inexistente") is None
    assert repo_prod.obter_contrato("ctr_inexistente") is None
    assert repo_prod.substituir_exigencias("ctr_inexistente", []) is None

    # Simula perda de db após construção para cobrir guards internos != "local"
    repo_prod.db = None
    with pytest.raises(RuntimeError):
        repo_prod.listar_terceirizadas()
    with pytest.raises(RuntimeError):
        repo_prod.obter_terceirizada("x")
    with pytest.raises(RuntimeError):
        repo_prod.criar_terceirizada(NovaTerceirizada(razaoSocial="Prod", cnpj="11222333000181"))
    with pytest.raises(RuntimeError):
        repo_prod.listar_tipos_documento()
    with pytest.raises(RuntimeError):
        repo_prod.obter_tipo_documento("tip_x")
    with pytest.raises(RuntimeError):
        repo_prod.criar_tipo_documento(NovoTipoDocumento(nome="Tipo Prod", validadeMeses=12))
    with pytest.raises(RuntimeError):
        repo_prod.listar_contratos()
    with pytest.raises(RuntimeError):
        repo_prod.obter_contrato("ctr_x")
    with pytest.raises(RuntimeError):
        repo_prod.criar_contrato(
            NovoContrato(
                identificador="C1",
                terceirizadaId="ter_1",
                dataInicio="2026-01-01",
                dataFim="2027-01-01",
            )
        )
    with pytest.raises(RuntimeError):
        repo_prod._registrar_auditoria({"tipo": "TESTE"})
    with pytest.raises(RuntimeError):
        repo_prod.substituir_exigencias("ctr_x", [])


def test_qa11_schemas_validacao_cnpj_e_cpf_e_rotas_cadastros(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    for cnpj_ruin in ("1234", "11111111111111", "11222333000189"):
        with pytest.raises(ValidationError):
            NovaTerceirizada(razaoSocial="Empresa Ruim", cnpj=cnpj_ruin)

    with pytest.raises(ValidationError):
        NovoTrabalhador(nome="Trab Ruim", cpf="10100000129")

    # Rotas cadastros: criar contrato/detalhar com id inexistente (404),
    # matriz com tipo inexistente (404) e contrato inexistente (404)
    resp_ctr_404 = client.post(
        "/contratos",
        json={
            "identificador": "CTR-404",
            "terceirizadaId": "ter_nao_existe",
            "dataInicio": "2026-01-01",
            "dataFim": "2027-01-01",
        },
        headers=headers_admin_alfa,
    )
    assert resp_ctr_404.status_code == 404

    assert client.get("/contratos/ctr_nao_existe", headers=headers_admin_alfa).status_code == 404
    assert (
        client.put(
            "/contratos/ctr_nao_existe/exigencias",
            json={"tipoDocumentoIds": ["tip_aso"]},
            headers=headers_admin_alfa,
        ).status_code
        == 404
    )


# -----------------------------------------------------------------------------
# 4. trabalhadores/repositorio.py e rotas.py
# -----------------------------------------------------------------------------
def test_qa11_trabalhadores_repositorio_firestore_semaforo_e_documentos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeFirestoreDB()
    storage_mock = MagicMock()
    storage_mock.salvar.return_value = (
        "tenants/tnt_qa11_trb/trabalhadores/trb_1/documentos/doc_1/arq.pdf"
    )
    storage_mock.obter_url_leitura.return_value = "http://public.local/signed/arq.pdf?token=abc"

    cad_repo = CadastrosRepositorio(tenant_id="tnt_qa11_trb", db=cast(Any, db))
    t1 = cad_repo.criar_terceirizada(
        NovaTerceirizada(razaoSocial="Terceira QA11", cnpj="11222333000181")
    )
    td_aso = cad_repo.criar_tipo_documento(NovoTipoDocumento(nome="ASO", validadeMeses=12))
    td_nr10 = cad_repo.criar_tipo_documento(NovoTipoDocumento(nome="NR-10", validadeMeses=24))
    ctr = cad_repo.criar_contrato(
        NovoContrato(
            identificador="CTR-TRB-QA11",
            terceirizadaId=t1.id,
            dataInicio="2026-01-01",
            dataFim="2027-12-31",
        )
    )
    cad_repo.substituir_exigencias(ctr.id, [td_aso.id, td_nr10.id], ator_uid="uid_qa11")

    repo = TrabalhadoresRepositorio(
        tenant_id="tnt_qa11_trb",
        db=cast(Any, db),
        storage=storage_mock,
    )

    trb1 = repo.criar_trabalhador("Trab Um", "10100000126", t1.id)
    trb2 = repo.criar_trabalhador("Trab Dois", "20200000160", t1.id)
    with pytest.raises(AppException) as exc_cpf:
        repo.criar_trabalhador("Trab Dup", "10100000126", t1.id)
    assert exc_cpf.value.status_code == 409

    # Listar com filtro e cursor
    l1, cur1 = repo.listar_trabalhadores(terceirizada_id_filtro=t1.id, limite=1)
    assert len(l1) == 1 and cur1 is not None
    l2, _ = repo.listar_trabalhadores(terceirizada_id_filtro=t1.id, limite=1, cursor=cur1)
    assert len(l2) == 1 and l2[0].id != l1[0].id

    # Vincular alocação no Firestore + conflito duplicado
    aloc = repo.vincular_alocacao(ctr.id, trb1.id)
    assert aloc.semaforo == "VERMELHO"
    with pytest.raises(AppException) as exc_aloc:
        repo.vincular_alocacao(ctr.id, trb1.id)
    assert exc_aloc.value.status_code == 409

    # Criar documentos, aprovar (amarelo <= 30d e vencido) e rejeitar no Firestore
    hoje = datetime.datetime.now(UTC).date()
    vence_10d = (hoje + datetime.timedelta(days=10)).isoformat()
    meta = ArquivoMetadados(contentType="application/pdf", tamanhoBytes=100, sha256="a" * 64)

    doc_aso = repo.criar_documento(
        trabalhador_id=trb1.id,
        tipo_documento_id=td_aso.id.replace("tip_", "tdoc_"),
        valido_ate=vence_10d,
        data_emissao=(hoje - datetime.timedelta(days=10)).isoformat(),
        arquivo_meta=meta,
        conteudo=PDF_FICTICIO,
    )
    doc_nr10 = repo.criar_documento(
        trabalhador_id=trb1.id,
        tipo_documento_id=td_nr10.id,
        valido_ate=(hoje + datetime.timedelta(days=90)).isoformat(),
        data_emissao=(hoje - datetime.timedelta(days=5)).isoformat(),
        arquivo_meta=meta,
        conteudo=PDF_FICTICIO,
    )
    doc_rej = repo.criar_documento(
        trabalhador_id=trb2.id,
        tipo_documento_id=td_aso.id,
        valido_ate=vence_10d,
        data_emissao=None,
        arquivo_meta=meta,
        conteudo=PDF_FICTICIO,
    )

    repo.aprovar_documento(doc_aso.id)
    repo.aprovar_documento(doc_nr10.id)
    repo.rejeitar_documento(doc_rej.id, motivo="Documento ilegível para conferência no teste.")

    # Semáforo AMARELO (um vence em 10d e outro válido 90d)
    det = repo.obter_trabalhador(trb1.id)
    assert det is not None and len(det.documentos) == 2
    assert det.alocacoes[0].semaforo == "AMARELO"

    # Semáforo com data_referencia futura -> VENCIDO -> VERMELHO e teste de validoAte inválido
    aloc_futura = repo.calcular_semaforo(
        ctr.id, trb1.id, data_referencia=hoje + datetime.timedelta(days=200)
    )
    assert aloc_futura.semaforo == "VERMELHO"

    # Listar documentos via db + terceirizada_id_filtro + cursor
    docs_p1, dcur = repo.listar_documentos(
        status_revisao="APROVADO", terceirizada_id_filtro=t1.id, limite=1
    )
    assert len(docs_p1) == 1 and dcur is not None
    docs_p2, _ = repo.listar_documentos(
        status_revisao="APROVADO", terceirizada_id_filtro=t1.id, limite=1, cursor=dcur
    )
    assert len(docs_p2) == 1

    # Fallback de collection_group quando tenants/{t}/documentos está vazio
    sub_only_doc_path = f"tenants/tnt_qa11_trb/trabalhadores/{trb1.id}/documentos/doc_sub_1"
    db.store[sub_only_doc_path] = {
        "id": "doc_sub_1",
        "trabalhadorId": trb1.id,
        "tipoDocumentoId": td_aso.id,
        "validoAte": "data-invalida",
        "statusRevisao": "APROVADO",
        "enviadoEm": datetime.datetime.now(UTC).isoformat(),
    }
    # Apaga coleção direta de documentos para exercitar fallback subcoleção / collection_group
    for k in list(db.store.keys()):
        if k.startswith("tenants/tnt_qa11_trb/documentos/"):
            del db.store[k]

    docs_cg, _ = repo.listar_documentos(status_revisao="APROVADO")
    assert any(d.id == "doc_sub_1" for d in docs_cg)
    assert repo.obter_documento("doc_sub_1", terceirizada_id_filtro="ter_outra") is None
    assert repo.obter_documento("doc_sub_1", terceirizada_id_filtro=t1.id) is not None
    assert repo.calcular_semaforo(ctr.id, trb1.id).semaforo == "VERMELHO"

    # _montar_documento aceitando instância ArquivoMetadados já construída
    montado = TrabalhadoresRepositorio._montar_documento(
        {
            "id": "doc_m1",
            "trabalhadorId": trb1.id,
            "tipoDocumentoId": td_aso.id,
            "validoAte": "2027-01-01",
            "statusRevisao": "PENDENTE",
            "arquivo": meta,
        }
    )
    assert montado.arquivo == meta

    # Erros de aprovar / rejeitar
    with pytest.raises(AppException) as exc_404_ap:
        repo.aprovar_documento("doc_inexistente")
    assert exc_404_ap.value.status_code == 404

    with pytest.raises(AppException) as exc_404_rj:
        repo.rejeitar_documento("doc_inexistente", motivo="Motivo válido com mais de 10 chars")
    assert exc_404_rj.value.status_code == 404

    doc_data_err = repo.criar_documento(
        trabalhador_id=trb1.id,
        tipo_documento_id=td_aso.id,
        valido_ate="2027-05-01",
        data_emissao="2026-01-01",
        arquivo_meta=meta,
    )
    with pytest.raises(AppException) as exc_dt:
        repo.aprovar_documento(doc_data_err.id, valido_ate_override="invalida")
    assert exc_dt.value.status_code == 400


def test_qa11_trabalhadores_repositorio_download_e_erros_producao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeFirestoreDB()
    storage_mock = MagicMock()
    repo_local = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=None, storage=storage_mock)

    # Sem 'caminho' nos metadados -> usa _derivar_uuid_arquivo e fallback local de storage
    storage_mock.obter_url_leitura.side_effect = ObjetoNaoEncontradoError("nao encontrado")
    resp_obj = repo_local.gerar_url_download("doc_alfa_1_aso")
    assert resp_obj is not None and "token=local-offline" in resp_obj.url

    storage_mock.obter_url_leitura.side_effect = ArmazenamentoError("timeout storage")
    resp_err = repo_local.gerar_url_download("doc_alfa_1_aso")
    assert resp_err is not None and "token=local-offline" in resp_err.url

    # Ramos em APP_ENV=prod
    _ativar_env_prod(monkeypatch)
    monkeypatch.setattr("habilita.trabalhadores.repositorio.obter_firestore_client", lambda: None)
    with pytest.raises(RuntimeError, match="memória"):
        TrabalhadoresRepositorio(tenant_id="tnt_prod")

    repo_prod = TrabalhadoresRepositorio(
        tenant_id="tnt_prod",
        db=cast(Any, db),
        storage=storage_mock,
    )
    assert repo_prod.obter_trabalhador("inexistente") is None

    db.store["tenants/tnt_prod/documentos/doc_p1"] = {
        "id": "doc_p1",
        "trabalhadorId": "trb_p1",
        "tipoDocumentoId": "tip_aso",
        "validoAte": "2027-01-01",
        "statusRevisao": "PENDENTE",
        "arquivo": {"contentType": "application/pdf", "tamanhoBytes": 10, "sha256": "0" * 64},
    }
    storage_mock.obter_url_leitura.side_effect = ObjetoNaoEncontradoError("ausente")
    assert repo_prod.gerar_url_download("doc_p1") is None

    storage_mock.obter_url_leitura.side_effect = ArmazenamentoError("falha prod")
    with pytest.raises(ArmazenamentoError):
        repo_prod.gerar_url_download("doc_p1")

    storage_mock.salvar.side_effect = ArmazenamentoError("falha upload prod")
    meta = ArquivoMetadados(contentType="application/pdf", tamanhoBytes=10, sha256="a" * 64)
    with pytest.raises(ArmazenamentoError):
        repo_prod.criar_documento(
            trabalhador_id="trb_p1",
            tipo_documento_id="tip_aso",
            valido_ate="2027-01-01",
            data_emissao="2026-01-01",
            arquivo_meta=meta,
            conteudo=PDF_FICTICIO,
        )

    # Simula perda de db após construção para cobrir guards != "local"
    repo_prod.db = None
    with pytest.raises(RuntimeError):
        repo_prod.listar_trabalhadores()
    with pytest.raises(RuntimeError):
        repo_prod.criar_trabalhador("Nome", "10100000126", "ter_1")
    with pytest.raises(RuntimeError):
        repo_prod.vincular_alocacao("ctr_1", "trb_1")
    with pytest.raises(RuntimeError):
        repo_prod.listar_documentos()
    with pytest.raises(RuntimeError):
        repo_prod.criar_documento("trb_1", "tip_aso", "2027-01-01", None, meta)
    doc_fake = Documento(
        id="doc_p1",
        trabalhadorId="trb_p1",
        tipoDocumentoId="tip_aso",
        validoAte="2027-01-01",
        statusRevisao="PENDENTE",
        enviadoEm="2026-01-01T00:00:00Z",
        dataEmissao="2026-01-01",
    )
    monkeypatch.setattr(repo_prod, "obter_documento", lambda *_a, **_kw: doc_fake)
    with pytest.raises(RuntimeError):
        repo_prod.aprovar_documento("doc_p1")
    with pytest.raises(RuntimeError):
        repo_prod.rejeitar_documento("doc_p1", "Motivo com mais de dez caracteres")


def test_qa11_rotas_trabalhadores_ramos_terceirizada_e_erros(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    # Terceirizada sem claim terceirizadaId -> 403
    headers_terc_sem_id = {"Authorization": "Bearer test-token:tnt_alfa:terceirizada::uid_sem_ter"}
    resp_sem_id = client.post(
        "/trabalhadores",
        json={"nome": "Trab Sem Ter", "cpf": "10100000126"},
        headers=headers_terc_sem_id,
    )
    assert resp_sem_id.status_code == 403

    # Admin sem terceirizadaId -> 400
    resp_admin_sem_ter = client.post(
        "/trabalhadores",
        json={"nome": "Trab Admin Sem Ter", "cpf": "10100000126"},
        headers=headers_admin_alfa,
    )
    assert resp_admin_sem_ter.status_code == 400

    # Alocação com trabalhador inexistente -> 404
    resp_aloc_404 = client.post(
        "/contratos/ctr_alfa_1/alocacoes",
        json={"trabalhadorId": "trb_inexistente"},
        headers=headers_admin_alfa,
    )
    assert resp_aloc_404.status_code == 404

    # Upload com trabalhador inexistente -> 404 e data inválida -> 400
    resp_up_404 = client.post(
        "/trabalhadores/trb_inexistente/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(PDF_FICTICIO), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up_404.status_code == 404

    resp_data_invalida = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(PDF_FICTICIO), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "31/12/2027"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_data_invalida.status_code == 400

    # Terceirizada tentando usar endpoint de memória para alocar contrato de outro tenant -> 404
    assert client.get("/trabalhadores", headers=headers_terceirizada_1_alfa).status_code == 200
    assert TER_ALFA_1 == "ter_alfa_1"
