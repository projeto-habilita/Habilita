"""Categoria 3 — Matriz de Autorização 100% das rotas e isolamento multi-tenant ([QA-08] / [QA-09]).

Cobre todas as 29 operações (25 caminhos) do `openapi.yaml` em 4 eixos:
1. Sem token -> 401 (exceto `/healthz` e `/readyz` públicas -> 200).
2. Token inválido, malformado ou expirado (sem headers `X-Simulated-*`) -> 401.
3. Cada perfil não autorizado dentre `admin_contratante`, `analista_sst`,
   `terceirizada`, `porteiro` (e segredo ausente/inválido no job interno) -> 403/401.
4. Acesso cross-tenant (`tnt_beta` <-> `tnt_alfa`) e cross-terceirizada
   (`ter_alfa_2` <-> `ter_alfa_1`) -> 404 (anti-enumeração conforme ADR 0002).

Suporta execução pós-deploy (`[QA-08]`) via variável `HABILITA_BASE_URL` (usando
`httpx.Client`) e usa `TestClient(app)` por padrão em ambiente local/CI.
"""

from __future__ import annotations

import datetime
import io
import os
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from habilita.main import app

from .conftest import (
    CPF_TRB_ALFA_1,
    CTR_ALFA_1,
    CTR_BETA_1,
    DOC_ALFA_1_ASO,
    DOC_BETA_1_ASO,
    TER_ALFA_1,
    TNT_ALFA,
    TNT_BETA,
    TRB_ALFA_1,
    TRB_ALFA_Y1,
    TRB_BETA_1,
    criar_headers_autenticados,
)

PAPEIS_USUARIOS: tuple[str, ...] = (
    "admin_contratante",
    "analista_sst",
    "terceirizada",
    "porteiro",
)

PDF_MINIMO = b"%PDF-1.4\n% DOCUMENTO FICTICIO - TESTE AUTOMATIZADO HABILITA\n%%EOF"


@dataclass(frozen=True)
class EspecificacaoRotaAuth:
    """Define uma operação OpenAPI e os dados mínimos válidos para testar autorização."""

    metodo: str
    caminho_template: str
    operation_id: str
    papeis_permitidos: tuple[str, ...]
    implementada: bool
    caminho_execucao: str
    query: dict[str, str] | None = None
    json_valido: dict[str, Any] | None = None
    multipart: bool = False


def _gerar_cpf_valido(raiz_9_digitos: str) -> str:
    base = raiz_9_digitos[:9]
    r1 = sum(int(base[i]) * (10 - i) for i in range(9)) % 11
    d1 = 0 if r1 < 2 else 11 - r1
    parcial = f"{base}{d1}"
    r2 = sum(int(parcial[i]) * (11 - i) for i in range(10)) % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return f"{parcial}{d2}"


def _gerar_cnpj_valido(raiz_8_digitos: str) -> str:
    base = f"{raiz_8_digitos[:8]}0001"
    pesos1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    r1 = sum(int(base[i]) * pesos1[i] for i in range(12)) % 11
    d1 = 0 if r1 < 2 else 11 - r1
    parcial = f"{base}{d1}"
    pesos2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    r2 = sum(int(parcial[i]) * pesos2[i] for i in range(13)) % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return f"{parcial}{d2}"


ROTAS_OPENAPI_AUTH: tuple[EspecificacaoRotaAuth, ...] = (
    EspecificacaoRotaAuth("GET", "/healthz", "healthz", ("publico",), True, "/healthz"),
    EspecificacaoRotaAuth("GET", "/readyz", "readyz", ("publico",), True, "/readyz"),
    EspecificacaoRotaAuth(
        "GET",
        "/me",
        "obterPerfil",
        ("admin_contratante", "analista_sst", "terceirizada", "porteiro"),
        True,
        "/me",
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/terceirizadas",
        "listarTerceirizadas",
        ("admin_contratante", "analista_sst"),
        True,
        "/terceirizadas",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/terceirizadas",
        "criarTerceirizada",
        ("admin_contratante",),
        True,
        "/terceirizadas",
        json_valido={
            "razaoSocial": "Empresa Matriz Autorizacao",
            "cnpj": _gerar_cnpj_valido("71000001"),
        },
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/tipos-documento",
        "listarTiposDocumento",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        "/tipos-documento",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/tipos-documento",
        "criarTipoDocumento",
        ("admin_contratante",),
        True,
        "/tipos-documento",
        json_valido={"nome": "NR Teste Auth", "validadeMeses": 12},
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/contratos",
        "listarContratos",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        "/contratos",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/contratos",
        "criarContrato",
        ("admin_contratante",),
        True,
        "/contratos",
        json_valido={
            "identificador": "CTR-AUTH-MATRIZ",
            "terceirizadaId": TER_ALFA_1,
            "dataInicio": "2026-01-01",
            "dataFim": "2027-12-31",
        },
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/contratos/{contratoId}",
        "obterContrato",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        f"/contratos/{CTR_ALFA_1}",
    ),
    EspecificacaoRotaAuth(
        "PUT",
        "/contratos/{contratoId}/exigencias",
        "definirMatrizExigencias",
        ("admin_contratante",),
        True,
        f"/contratos/{CTR_ALFA_1}/exigencias",
        json_valido={"tipoDocumentoIds": ["tip_aso"]},
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/contratos/{contratoId}/alocacoes",
        "alocarTrabalhador",
        ("admin_contratante", "terceirizada"),
        True,
        f"/contratos/{CTR_ALFA_1}/alocacoes",
        json_valido={"trabalhadorId": TRB_ALFA_1},
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/contratos/{contratoId}/conformidade",
        "obterConformidadeContrato",
        ("admin_contratante", "analista_sst", "terceirizada"),
        False,
        f"/contratos/{CTR_ALFA_1}/conformidade",
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/painel/contratos",
        "listarPainelContratos",
        ("admin_contratante", "analista_sst"),
        False,
        "/painel/contratos",
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/trabalhadores",
        "listarTrabalhadores",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        "/trabalhadores",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/trabalhadores",
        "criarTrabalhador",
        ("admin_contratante", "terceirizada"),
        True,
        "/trabalhadores",
        json_valido={
            "nome": "Trabalhador Auth Matriz",
            "cpf": _gerar_cpf_valido("710000101"),
            "terceirizadaId": TER_ALFA_1,
        },
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/trabalhadores/{trabalhadorId}",
        "obterTrabalhador",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        f"/trabalhadores/{TRB_ALFA_1}",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/trabalhadores/{trabalhadorId}/documentos",
        "enviarDocumento",
        ("terceirizada", "admin_contratante"),
        True,
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        multipart=True,
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/documentos",
        "listarDocumentos",
        ("admin_contratante", "analista_sst"),
        True,
        "/documentos",
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/documentos/{documentoId}",
        "obterDocumento",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        f"/documentos/{DOC_ALFA_1_ASO}",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/documentos/{documentoId}/url-download",
        "gerarUrlDownload",
        ("admin_contratante", "analista_sst", "terceirizada"),
        True,
        f"/documentos/{DOC_ALFA_1_ASO}/url-download",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/documentos/{documentoId}/aprovacao",
        "aprovarDocumento",
        ("analista_sst", "admin_contratante"),
        True,
        f"/documentos/{DOC_ALFA_1_ASO}/aprovacao",
        json_valido={},
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/documentos/{documentoId}/rejeicao",
        "rejeitarDocumento",
        ("analista_sst", "admin_contratante"),
        True,
        f"/documentos/{DOC_ALFA_1_ASO}/rejeicao",
        json_valido={"motivo": "Documento ilegível na conferência de assinatura"},
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/alertas",
        "listarAlertas",
        ("admin_contratante", "analista_sst"),
        False,
        "/alertas",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/alertas/{alertaId}/leitura",
        "marcarAlertaLido",
        ("admin_contratante", "analista_sst"),
        False,
        "/alertas/alr_ficticio_1/leitura",
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/liberacoes/consultas",
        "consultarLiberacao",
        ("porteiro", "admin_contratante", "analista_sst"),
        False,
        "/liberacoes/consultas",
        json_valido={"cpf": CPF_TRB_ALFA_1},
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/acessos",
        "registrarAcesso",
        ("porteiro",),
        False,
        "/acessos",
        json_valido={"consultaId": "cns_ficticio_1", "decisao": "BARRADO"},
    ),
    EspecificacaoRotaAuth(
        "GET",
        "/auditoria",
        "listarAuditoria",
        ("admin_contratante",),
        False,
        "/auditoria",
        query={"de": "2026-11-01", "ate": "2026-11-15"},
    ),
    EspecificacaoRotaAuth(
        "POST",
        "/internal/jobs/recalculo-vencimentos",
        "executarRecalculoVencimentos",
        ("job-agendado",),
        False,
        "/internal/jobs/recalculo-vencimentos",
        json_valido={"dataReferencia": "2026-11-26"},
    ),
)

DIVERGENCIAS_X_PAPEIS_IMPLEMENTADAS: dict[tuple[str, str], str] = {
    ("criarTrabalhador", "analista_sst"): (
        "Defeito de contrato: POST /trabalhadores permite 'analista_sst' em "
        "src/habilita/trabalhadores/rotas.py, mas openapi.yaml restringe x-papeis "
        "a [admin_contratante, terceirizada]."
    ),
    ("enviarDocumento", "analista_sst"): (
        "Defeito de contrato: POST /trabalhadores/{trabalhadorId}/documentos permite "
        "'analista_sst' em src/habilita/trabalhadores/rotas.py, mas openapi.yaml "
        "restringe x-papeis a [terceirizada, admin_contratante]."
    ),
}


@pytest.fixture
def cliente_http() -> Generator[TestClient | httpx.Client, None, None]:
    """Usa `httpx.Client` com `HABILITA_BASE_URL` ([QA-08]) ou `TestClient` por padrão."""
    base_url = os.getenv("HABILITA_BASE_URL", "").strip()
    if base_url:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=15.0) as http_client:
            yield http_client
    else:
        with TestClient(app) as test_client:
            yield test_client


def _headers_para_papel(
    papel: str,
    tenant_id: str = TNT_ALFA,
    terc_id: str | None = None,
) -> dict[str, str]:
    """Obtém cabeçalho Bearer por papel (suportando tokens reais via env para `[QA-08]`)."""
    if os.getenv("HABILITA_BASE_URL", "").strip():
        mapa_env = {
            ("admin_contratante", TNT_ALFA): "HABILITA_TOKEN_ADMIN",
            ("analista_sst", TNT_ALFA): "HABILITA_TOKEN_ANALISTA",
            ("terceirizada", TNT_ALFA): "HABILITA_TOKEN_TERCEIRIZADA",
            ("porteiro", TNT_ALFA): "HABILITA_TOKEN_PORTEIRO",
            ("admin_contratante", TNT_BETA): "HABILITA_TOKEN_ADMIN_B",
            ("analista_sst", TNT_BETA): "HABILITA_TOKEN_ANALISTA_B",
            ("terceirizada", TNT_BETA): "HABILITA_TOKEN_TERCEIRIZADA_B",
        }
        token_env = os.getenv(mapa_env.get((papel, tenant_id), ""), "").strip()
        if token_env:
            return {"Authorization": f"Bearer {token_env}"}

    terc_padrao = TER_ALFA_1 if papel == "terceirizada" and tenant_id == TNT_ALFA else None
    terc_final = terc_id or terc_padrao
    return criar_headers_autenticados(
        tenant_id=tenant_id,
        papel=papel,
        terceirizada_id=terc_final,
        uid=f"uid_{papel}_{tenant_id}",
    )


def _enviar_requisicao(
    cliente: TestClient | httpx.Client,
    spec: EspecificacaoRotaAuth,
    headers: dict[str, str] | None = None,
    caminho_override: str | None = None,
) -> httpx.Response:
    """Executa a operação usando o payload válido adequado para alcançar a camada de auth/authz."""
    caminho = caminho_override or spec.caminho_execucao
    hdrs = headers or {}
    if spec.multipart:
        amanha = (datetime.date.today() + datetime.timedelta(days=180)).isoformat()
        return cliente.request(
            spec.metodo,
            caminho,
            headers=hdrs,
            files={"arquivo": ("doc.pdf", io.BytesIO(PDF_MINIMO), "application/pdf")},
            data={"tipoDocumentoId": "tip_aso", "validoAte": amanha},
        )
    return cliente.request(
        spec.metodo,
        caminho,
        headers=hdrs,
        params=spec.query,
        json=spec.json_valido,
    )


def _assert_erro_problem_details(
    resp: httpx.Response,
    status_esperado: int,
    *,
    exigir_rota_implementada: bool = False,
) -> None:
    assert resp.status_code == status_esperado, (
        f"Esperado HTTP {status_esperado}, obtido {resp.status_code}: {resp.text}"
    )
    assert "application/problem+json" in resp.headers.get("content-type", "")
    corpo = resp.json()
    assert corpo.get("status") == status_esperado
    assert "title" in corpo and "type" in corpo
    if exigir_rota_implementada:
        assert corpo.get("detail") != "Not Found", (
            "Resposta 404 veio do roteador genérico (rota ainda não implementada)."
        )


# -----------------------------------------------------------------------------
# Eixo 1: Sem token -> 401 (exceto /healthz e /readyz -> 200)
# -----------------------------------------------------------------------------
def _casos_eixo1_sem_token() -> list[Any]:
    casos: list[Any] = []
    for spec in ROTAS_OPENAPI_AUTH:
        marks: list[Any] = []
        if not spec.implementada:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=(
                        f"Rota J4/J5/Interno {spec.metodo} {spec.caminho_template} "
                        "ainda não implementada."
                    ),
                )
            )
        casos.append(pytest.param(spec, id=f"{spec.metodo}_{spec.operation_id}", marks=marks))
    return casos


@pytest.mark.parametrize("spec", _casos_eixo1_sem_token())
def test_eixo1_sem_token_retorna_401_ou_200_publico(
    cliente_http: TestClient | httpx.Client,
    spec: EspecificacaoRotaAuth,
) -> None:
    """Sem Authorization: rotas públicas retornam 200 e protegidas retornam 401 Problem Details."""
    resp = _enviar_requisicao(cliente_http, spec, headers={})
    if "publico" in spec.papeis_permitidos:
        assert resp.status_code == 200
    else:
        _assert_erro_problem_details(resp, 401)


# -----------------------------------------------------------------------------
# Eixo 2: Token inválido / malformado / expirado -> 401 (zero X-Simulated-*)
# -----------------------------------------------------------------------------
TOKENS_INVALIDOS: tuple[tuple[str, dict[str, str]], ...] = (
    ("malformado", {"Authorization": "Bearer token-corrompido-sem-formato"}),
    (
        "jwt_expirado_estrutura",
        {"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjF9.assinatura-expirada"},
    ),
    ("esquema_basic", {"Authorization": "Basic dXN1YXJpbzpzZW5oYQ=="}),
    ("bearer_vazio", {"Authorization": "Bearer "}),
)


def _casos_eixo2_token_invalido() -> list[Any]:
    casos: list[Any] = []
    for spec in ROTAS_OPENAPI_AUTH:
        if "publico" in spec.papeis_permitidos:
            continue
        for nome_tok, hdr in TOKENS_INVALIDOS:
            marks: list[Any] = []
            if not spec.implementada:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        reason=(
                            f"Rota J4/J5/Interno {spec.metodo} {spec.caminho_template} "
                            "ainda não implementada."
                        ),
                    )
                )
            casos.append(
                pytest.param(
                    spec,
                    hdr,
                    id=f"{spec.metodo}_{spec.operation_id}__{nome_tok}",
                    marks=marks,
                )
            )
    return casos


@pytest.mark.parametrize(("spec", "headers_invalidos"), _casos_eixo2_token_invalido())
def test_eixo2_token_invalido_ou_expirado_retorna_401(
    cliente_http: TestClient | httpx.Client,
    spec: EspecificacaoRotaAuth,
    headers_invalidos: dict[str, str],
) -> None:
    """Tokens inválidos, malformados ou expirados retornam 401 Problem Details."""
    resp = _enviar_requisicao(cliente_http, spec, headers=headers_invalidos)
    _assert_erro_problem_details(resp, 401)


# -----------------------------------------------------------------------------
# Eixo 3: Cada perfil não autorizado -> 403 (e job interno -> 401)
# -----------------------------------------------------------------------------
def _casos_eixo3_perfil_nao_autorizado() -> list[Any]:
    casos: list[Any] = []
    for spec in ROTAS_OPENAPI_AUTH:
        if "publico" in spec.papeis_permitidos:
            continue
        for papel in PAPEIS_USUARIOS:
            if papel in spec.papeis_permitidos:
                continue
            status_esperado = 401 if "job-agendado" in spec.papeis_permitidos else 403
            marks: list[Any] = []
            if not spec.implementada:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        reason=(
                            f"Rota J4/J5/Interno {spec.metodo} {spec.caminho_template} "
                            "ainda não implementada."
                        ),
                    )
                )
            elif (spec.operation_id, papel) in DIVERGENCIAS_X_PAPEIS_IMPLEMENTADAS:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        reason=DIVERGENCIAS_X_PAPEIS_IMPLEMENTADAS[(spec.operation_id, papel)],
                    )
                )
            casos.append(
                pytest.param(
                    spec,
                    papel,
                    status_esperado,
                    id=f"{spec.metodo}_{spec.operation_id}__negado_{papel}",
                    marks=marks,
                )
            )
    return casos


@pytest.mark.parametrize(
    ("spec", "papel_negado", "status_esperado"),
    _casos_eixo3_perfil_nao_autorizado(),
)
def test_eixo3_perfil_nao_autorizado_retorna_403(
    cliente_http: TestClient | httpx.Client,
    spec: EspecificacaoRotaAuth,
    papel_negado: str,
    status_esperado: int,
) -> None:
    """Todo perfil fora de `x-papeis` deve receber 403 (ou 401 no job interno) Problem Details."""
    headers = _headers_para_papel(papel_negado, tenant_id=TNT_ALFA)
    resp = _enviar_requisicao(cliente_http, spec, headers=headers)
    _assert_erro_problem_details(resp, status_esperado)


@pytest.mark.xfail(
    strict=True,
    reason="Rota interna POST /internal/jobs/recalculo-vencimentos ainda não implementada.",
)
@pytest.mark.parametrize(
    "headers_job",
    [
        {},
        {"Authorization": "Bearer segredo-incorreto"},
        {"Authorization": "Bearer "},
    ],
    ids=["sem_segredo", "segredo_invalido", "segredo_vazio"],
)
def test_eixo3_job_interno_segredo_ausente_ou_invalido_retorna_401(
    cliente_http: TestClient | httpx.Client,
    headers_job: dict[str, str],
) -> None:
    """Job interno exige `JOB_TOKEN` válido e retorna 401 Problem Details para segredo inválido."""
    spec = next(s for s in ROTAS_OPENAPI_AUTH if s.operation_id == "executarRecalculoVencimentos")
    resp = _enviar_requisicao(cliente_http, spec, headers=headers_job)
    _assert_erro_problem_details(resp, 401)


# -----------------------------------------------------------------------------
# Eixo 4: Isolamento cross-tenant e cross-terceirizada -> 404 (ADR 0002)
# -----------------------------------------------------------------------------
def _espec_por_id(op_id: str) -> EspecificacaoRotaAuth:
    return next(s for s in ROTAS_OPENAPI_AUTH if s.operation_id == op_id)


@pytest.mark.parametrize(
    ("op_id", "papel", "tenant_atacante", "caminho_alvo", "terc_atacante"),
    [
        ("obterContrato", "admin_contratante", TNT_BETA, f"/contratos/{CTR_ALFA_1}", None),
        (
            "definirMatrizExigencias",
            "admin_contratante",
            TNT_BETA,
            f"/contratos/{CTR_ALFA_1}/exigencias",
            None,
        ),
        (
            "alocarTrabalhador",
            "admin_contratante",
            TNT_BETA,
            f"/contratos/{CTR_ALFA_1}/alocacoes",
            None,
        ),
        ("obterTrabalhador", "admin_contratante", TNT_BETA, f"/trabalhadores/{TRB_ALFA_1}", None),
        (
            "enviarDocumento",
            "admin_contratante",
            TNT_BETA,
            f"/trabalhadores/{TRB_ALFA_1}/documentos",
            None,
        ),
        ("obterDocumento", "analista_sst", TNT_BETA, f"/documentos/{DOC_ALFA_1_ASO}", None),
        (
            "gerarUrlDownload",
            "analista_sst",
            TNT_BETA,
            f"/documentos/{DOC_ALFA_1_ASO}/url-download",
            None,
        ),
        (
            "aprovarDocumento",
            "analista_sst",
            TNT_BETA,
            f"/documentos/{DOC_ALFA_1_ASO}/aprovacao",
            None,
        ),
        (
            "rejeitarDocumento",
            "analista_sst",
            TNT_BETA,
            f"/documentos/{DOC_ALFA_1_ASO}/rejeicao",
            None,
        ),
        ("obterContrato", "admin_contratante", TNT_ALFA, f"/contratos/{CTR_BETA_1}", None),
        ("obterTrabalhador", "admin_contratante", TNT_ALFA, f"/trabalhadores/{TRB_BETA_1}", None),
        ("obterDocumento", "analista_sst", TNT_ALFA, f"/documentos/{DOC_BETA_1_ASO}", None),
        (
            "gerarUrlDownload",
            "analista_sst",
            TNT_ALFA,
            f"/documentos/{DOC_BETA_1_ASO}/url-download",
            None,
        ),
        pytest.param(
            "obterConformidadeContrato",
            "admin_contratante",
            TNT_BETA,
            f"/contratos/{CTR_ALFA_1}/conformidade",
            None,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Rota J4 GET /contratos/{contratoId}/conformidade ainda não implementada.",
            ),
        ),
        pytest.param(
            "marcarAlertaLido",
            "admin_contratante",
            TNT_BETA,
            "/alertas/alr_alfa_1/leitura",
            None,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Rota J4 POST /alertas/{alertaId}/leitura ainda não implementada.",
            ),
        ),
    ],
    ids=lambda val: str(val) if isinstance(val, str) else None,
)
def test_eixo4_acesso_cross_tenant_retorna_404_anti_enumeracao(
    cliente_http: TestClient | httpx.Client,
    op_id: str,
    papel: str,
    tenant_atacante: str,
    caminho_alvo: str,
    terc_atacante: str | None,
) -> None:
    """Usuário de `contratante_b` acessando recurso de `contratante_a` (e vice-versa) recebe 404."""
    spec = _espec_por_id(op_id)
    headers = _headers_para_papel(papel, tenant_id=tenant_atacante, terc_id=terc_atacante)
    resp = _enviar_requisicao(cliente_http, spec, headers=headers, caminho_override=caminho_alvo)
    _assert_erro_problem_details(resp, 404, exigir_rota_implementada=not spec.implementada)


@pytest.mark.parametrize(
    ("op_id", "terc_atacante", "caminho_alvo"),
    [
        ("obterContrato", "ter_alfa_2", f"/contratos/{CTR_ALFA_1}"),
        ("alocarTrabalhador", "ter_alfa_2", f"/contratos/{CTR_ALFA_1}/alocacoes"),
        ("obterTrabalhador", "ter_alfa_2", f"/trabalhadores/{TRB_ALFA_1}"),
        ("enviarDocumento", "ter_alfa_2", f"/trabalhadores/{TRB_ALFA_1}/documentos"),
        ("obterDocumento", "ter_alfa_2", f"/documentos/{DOC_ALFA_1_ASO}"),
        ("gerarUrlDownload", "ter_alfa_2", f"/documentos/{DOC_ALFA_1_ASO}/url-download"),
        ("obterTrabalhador", TER_ALFA_1, f"/trabalhadores/{TRB_ALFA_Y1}"),
        ("enviarDocumento", TER_ALFA_1, f"/trabalhadores/{TRB_ALFA_Y1}/documentos"),
        pytest.param(
            "obterConformidadeContrato",
            "ter_alfa_2",
            f"/contratos/{CTR_ALFA_1}/conformidade",
            marks=pytest.mark.xfail(
                strict=True,
                reason="Rota J4 GET /contratos/{contratoId}/conformidade ainda não implementada.",
            ),
        ),
    ],
)
def test_eixo4_acesso_cross_terceirizada_retorna_404_anti_enumeracao(
    cliente_http: TestClient | httpx.Client,
    op_id: str,
    terc_atacante: str,
    caminho_alvo: str,
) -> None:
    """Terceirizada B acessando recurso de Terceirizada A (mesmo tenant) recebe 404."""
    spec = _espec_por_id(op_id)
    headers = _headers_para_papel("terceirizada", tenant_id=TNT_ALFA, terc_id=terc_atacante)
    resp = _enviar_requisicao(cliente_http, spec, headers=headers, caminho_override=caminho_alvo)
    _assert_erro_problem_details(resp, 404, exigir_rota_implementada=not spec.implementada)


def test_eixo4_listagens_isoladas_por_tenant_e_terceirizada(
    cliente_http: TestClient | httpx.Client,
) -> None:
    """Listagens não devem vazar recursos de outro tenant nem de outra terceirizada."""
    headers_beta = _headers_para_papel("admin_contratante", tenant_id=TNT_BETA)
    headers_terc2 = _headers_para_papel("terceirizada", tenant_id=TNT_ALFA, terc_id="ter_alfa_2")

    resp_ctrs_beta = cliente_http.get("/contratos", headers=headers_beta)
    assert resp_ctrs_beta.status_code == 200
    assert all(c["id"] != CTR_ALFA_1 for c in resp_ctrs_beta.json()["itens"])

    resp_trbs_beta = cliente_http.get("/trabalhadores", headers=headers_beta)
    assert resp_trbs_beta.status_code == 200
    assert all(t["id"] != TRB_ALFA_1 for t in resp_trbs_beta.json()["itens"])

    resp_docs_beta = cliente_http.get("/documentos", headers=headers_beta)
    assert resp_docs_beta.status_code == 200
    assert all(d["id"] != DOC_ALFA_1_ASO for d in resp_docs_beta.json()["itens"])

    resp_ctrs_terc2 = cliente_http.get("/contratos", headers=headers_terc2)
    assert resp_ctrs_terc2.status_code == 200
    assert all(c["terceirizadaId"] == "ter_alfa_2" for c in resp_ctrs_terc2.json()["itens"])

    resp_trbs_terc2 = cliente_http.get("/trabalhadores", headers=headers_terc2)
    assert resp_trbs_terc2.status_code == 200
    assert all(t["terceirizadaId"] == "ter_alfa_2" for t in resp_trbs_terc2.json()["itens"])
