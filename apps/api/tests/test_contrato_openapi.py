"""Categoria 2 — Testes de Contrato OpenAPI e conformidade RFC 9457.

Valida:
- Alinhamento entre o contrato canônico de 25 caminhos / 29 operações do `openapi.yaml`
  (e leitura direta do arquivo YAML quando disponível via `HABILITA_OPENAPI_PATH` ou
  `../../../../habilita-docs/openapi.yaml`) e o esquema `/openapi.json` gerado pelo FastAPI.
- Chamadas HTTP reais via `TestClient` validando métodos, códigos de status 2xx e
  estruturas de resposta dos endpoints implementados.
- Conformidade estrita RFC 9457 (`application/problem+json` com `type`, `title`,
  `status`, `detail`, `instance` e cabeçalho `X-Request-Id`) em erros 4xx e 5xx.
- Rotas J4/J5/Auditoria/Interno ainda ausentes e divergências de contrato conhecidas
  marcadas com `@pytest.mark.xfail(strict=True, reason="...")`.
"""

from __future__ import annotations

import datetime
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    CTR_ALFA_1,
    CTR_BETA_1,
    DOC_ALFA_1_ASO,
    TER_ALFA_1,
    TRB_ALFA_1,
)

PDF_FICTICIO = b"%PDF-1.4\n% DOCUMENTO FICTICIO - TESTE AUTOMATIZADO HABILITA\n%%EOF"
JPEG_FICTICIO = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


@dataclass(frozen=True)
class OperacaoOpenAPI:
    """Metadados canônicos de uma operação definida no `openapi.yaml`."""

    metodo: str
    caminho: str
    operation_id: str
    papeis: tuple[str, ...]
    status_sucesso: int
    respostas_esperadas: tuple[str, ...]
    implementada: bool


CONTRATO_CANONICO_OPENAPI: tuple[OperacaoOpenAPI, ...] = (
    OperacaoOpenAPI("GET", "/healthz", "healthz", ("publico",), 200, ("200",), True),
    OperacaoOpenAPI("GET", "/readyz", "readyz", ("publico",), 200, ("200", "503"), True),
    OperacaoOpenAPI(
        "GET",
        "/me",
        "obterPerfil",
        ("admin_contratante", "analista_sst", "terceirizada", "porteiro"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/terceirizadas",
        "listarTerceirizadas",
        ("admin_contratante", "analista_sst"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/terceirizadas",
        "criarTerceirizada",
        ("admin_contratante",),
        201,
        ("201", "400", "401", "403", "409"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/tipos-documento",
        "listarTiposDocumento",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/tipos-documento",
        "criarTipoDocumento",
        ("admin_contratante",),
        201,
        ("201", "400", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/contratos",
        "listarContratos",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/contratos",
        "criarContrato",
        ("admin_contratante",),
        201,
        ("201", "400", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/contratos/{contratoId}",
        "obterContrato",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403", "404"),
        True,
    ),
    OperacaoOpenAPI(
        "PUT",
        "/contratos/{contratoId}/exigencias",
        "definirMatrizExigencias",
        ("admin_contratante",),
        200,
        ("200", "400", "401", "403", "404"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/contratos/{contratoId}/alocacoes",
        "alocarTrabalhador",
        ("admin_contratante", "terceirizada"),
        201,
        ("201", "400", "401", "403", "404", "409"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/contratos/{contratoId}/conformidade",
        "obterConformidadeContrato",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403", "404"),
        False,
    ),
    OperacaoOpenAPI(
        "GET",
        "/painel/contratos",
        "listarPainelContratos",
        ("admin_contratante", "analista_sst"),
        200,
        ("200", "401", "403"),
        False,
    ),
    OperacaoOpenAPI(
        "GET",
        "/trabalhadores",
        "listarTrabalhadores",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/trabalhadores",
        "criarTrabalhador",
        ("admin_contratante", "terceirizada"),
        201,
        ("201", "400", "401", "403", "409"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/trabalhadores/{trabalhadorId}",
        "obterTrabalhador",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403", "404"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/trabalhadores/{trabalhadorId}/documentos",
        "enviarDocumento",
        ("terceirizada", "admin_contratante"),
        201,
        ("201", "400", "401", "403", "404", "413", "415"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/documentos",
        "listarDocumentos",
        ("admin_contratante", "analista_sst"),
        200,
        ("200", "401", "403"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/documentos/{documentoId}",
        "obterDocumento",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403", "404"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/documentos/{documentoId}/url-download",
        "gerarUrlDownload",
        ("admin_contratante", "analista_sst", "terceirizada"),
        200,
        ("200", "401", "403", "404", "410"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/documentos/{documentoId}/aprovacao",
        "aprovarDocumento",
        ("analista_sst", "admin_contratante"),
        200,
        ("200", "400", "401", "403", "404", "409"),
        True,
    ),
    OperacaoOpenAPI(
        "POST",
        "/documentos/{documentoId}/rejeicao",
        "rejeitarDocumento",
        ("analista_sst", "admin_contratante"),
        200,
        ("200", "400", "401", "403", "404", "409"),
        True,
    ),
    OperacaoOpenAPI(
        "GET",
        "/alertas",
        "listarAlertas",
        ("admin_contratante", "analista_sst"),
        200,
        ("200", "401", "403"),
        False,
    ),
    OperacaoOpenAPI(
        "POST",
        "/alertas/{alertaId}/leitura",
        "marcarAlertaLido",
        ("admin_contratante", "analista_sst"),
        204,
        ("204", "401", "403", "404"),
        False,
    ),
    OperacaoOpenAPI(
        "POST",
        "/liberacoes/consultas",
        "consultarLiberacao",
        ("porteiro", "admin_contratante", "analista_sst"),
        200,
        ("200", "400", "401", "403", "429"),
        False,
    ),
    OperacaoOpenAPI(
        "POST",
        "/acessos",
        "registrarAcesso",
        ("porteiro",),
        201,
        ("201", "400", "401", "403", "404", "409", "422"),
        False,
    ),
    OperacaoOpenAPI(
        "GET",
        "/auditoria",
        "listarAuditoria",
        ("admin_contratante",),
        200,
        ("200", "400", "401", "403"),
        False,
    ),
    OperacaoOpenAPI(
        "POST",
        "/internal/jobs/recalculo-vencimentos",
        "executarRecalculoVencimentos",
        ("job-agendado",),
        200,
        ("200", "401"),
        False,
    ),
)


def _normalizar_caminho(caminho: str) -> str:
    """Normaliza nomes de parâmetros de rota (`{contratoId}` / `{contrato_id}` -> `{param}`)."""
    return re.sub(r"\{[^}]+\}", "{param}", caminho)


def _localizar_openapi_yaml() -> Path | None:
    """Retorna o caminho do `openapi.yaml` se disponível via env ou repositório irmão."""
    env_path = os.getenv("HABILITA_OPENAPI_PATH", "").strip()
    if env_path:
        candidato = Path(env_path)
        if candidato.is_file():
            return candidato

    pais = Path(__file__).resolve().parents
    if len(pais) > 4:
        relativo = pais[4] / "habilita-docs" / "openapi.yaml"
        if relativo.is_file():
            return relativo
    return None


def _extrair_operacoes_yaml(conteudo: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Extrai as operações do bloco `paths:` de `openapi.yaml` sem dependências externas."""
    operacoes: dict[tuple[str, str], dict[str, Any]] = {}
    in_paths = False
    in_responses = False
    caminho_atual: str | None = None
    chave_atual: tuple[str, str] | None = None

    for linha in conteudo.splitlines():
        if linha == "paths:":
            in_paths = True
            continue
        if in_paths and linha and not linha.startswith(" "):
            break
        if not in_paths:
            continue

        m_caminho = re.match(r"^  (/.*):$", linha)
        if m_caminho:
            caminho_atual = m_caminho.group(1)
            chave_atual = None
            in_responses = False
            continue

        m_metodo = re.match(r"^    (get|post|put|patch|delete):$", linha)
        if m_metodo and caminho_atual is not None:
            metodo = m_metodo.group(1).upper()
            chave_atual = (metodo, caminho_atual)
            operacoes[chave_atual] = {
                "operationId": "",
                "x-papeis": (),
                "responses": [],
            }
            in_responses = False
            continue

        if chave_atual is None:
            continue

        m_op_id = re.match(r"^      operationId:\s*(\S+)", linha)
        if m_op_id:
            operacoes[chave_atual]["operationId"] = m_op_id.group(1)
            continue

        m_papeis = re.match(r"^      x-papeis:\s*\[([^\]]+)\]", linha)
        if m_papeis:
            operacoes[chave_atual]["x-papeis"] = tuple(
                p.strip() for p in m_papeis.group(1).split(",")
            )
            continue

        if linha == "      responses:":
            in_responses = True
            continue

        if in_responses:
            if re.match(r"^      \S", linha):
                in_responses = False
            else:
                m_resp = re.match(r"^        '(\d{3})':", linha)
                if m_resp:
                    operacoes[chave_atual]["responses"].append(m_resp.group(1))

    return operacoes


def _gerar_cnpj_valido(raiz_8_digitos: str) -> str:
    """Gera CNPJ válido de 14 dígitos para uso exclusivo em massa de testes fictícia."""
    base = f"{raiz_8_digitos[:8]}0001"
    pesos1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    r1 = sum(int(base[i]) * pesos1[i] for i in range(12)) % 11
    d1 = 0 if r1 < 2 else 11 - r1
    parcial = f"{base}{d1}"
    pesos2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    r2 = sum(int(parcial[i]) * pesos2[i] for i in range(13)) % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return f"{parcial}{d2}"


def _gerar_cpf_valido(raiz_9_digitos: str) -> str:
    """Gera CPF matemático fictício de 11 dígitos para massa de testes."""
    base = raiz_9_digitos[:9]
    r1 = sum(int(base[i]) * (10 - i) for i in range(9)) % 11
    d1 = 0 if r1 < 2 else 11 - r1
    parcial = f"{base}{d1}"
    r2 = sum(int(parcial[i]) * (11 - i) for i in range(10)) % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return f"{parcial}{d2}"


def _assert_problem_details_rfc9457(
    resposta: Any,
    status_esperado: int,
    caminho_esperado: str,
) -> dict[str, Any]:
    """Valida todos os requisitos estruturais de erro RFC 9457 (Problem Details)."""
    assert resposta.status_code == status_esperado
    content_type = resposta.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"Esperado application/problem+json, obtido {content_type!r}"
    )
    assert resposta.headers.get("x-request-id"), "Cabeçalho X-Request-Id ausente no erro"

    corpo: dict[str, Any] = resposta.json()
    assert isinstance(corpo.get("type"), str) and corpo["type"]
    assert isinstance(corpo.get("title"), str) and corpo["title"]
    assert corpo.get("status") == status_esperado
    assert isinstance(corpo.get("detail"), str) and corpo["detail"]
    assert corpo.get("instance") == caminho_esperado
    assert isinstance(corpo.get("requestId"), str) and corpo["requestId"]
    return corpo


# -----------------------------------------------------------------------------
# Validação do contrato canônico e comparação direta com openapi.yaml (se presente)
# -----------------------------------------------------------------------------
def test_tabela_canonica_cobre_25_caminhos_e_29_operacoes_do_openapi() -> None:
    """Garante integridade da tabela canônica e compara contra openapi.yaml se disponível."""
    caminhos_unicos = {op.caminho for op in CONTRATO_CANONICO_OPENAPI}
    chaves_unicas = {(op.metodo, op.caminho) for op in CONTRATO_CANONICO_OPENAPI}
    ids_unicos = {op.operation_id for op in CONTRATO_CANONICO_OPENAPI}

    assert len(caminhos_unicos) == 25
    assert len(CONTRATO_CANONICO_OPENAPI) == 29
    assert len(chaves_unicas) == 29
    assert len(ids_unicos) == 29

    for op in CONTRATO_CANONICO_OPENAPI:
        if "publico" not in op.papeis:
            assert "401" in op.respostas_esperadas

    arquivo_yaml = _localizar_openapi_yaml()
    if arquivo_yaml is not None:
        operacoes_yaml = _extrair_operacoes_yaml(arquivo_yaml.read_text(encoding="utf-8"))
        assert len(operacoes_yaml) == 29
        for op in CONTRATO_CANONICO_OPENAPI:
            chave = (op.metodo, op.caminho)
            assert chave in operacoes_yaml, f"Operação ausente em {arquivo_yaml}: {chave}"
            dados_yaml = operacoes_yaml[chave]
            assert dados_yaml["operationId"] == op.operation_id
            assert dados_yaml["x-papeis"] == op.papeis
            assert tuple(dados_yaml["responses"]) == op.respostas_esperadas


# -----------------------------------------------------------------------------
# Validação contra /openapi.json da aplicação FastAPI
# -----------------------------------------------------------------------------
def test_openapi_json_disponivel_e_com_metadados_do_contrato(client: TestClient) -> None:
    """GET /openapi.json deve responder 200 com título e versão do contrato."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")
    schema = resp.json()
    assert schema["info"]["title"] == "Habilita API"
    assert schema["info"]["version"] == "0.2.0"
    assert "paths" in schema


@pytest.mark.parametrize(
    "operacao",
    [op for op in CONTRATO_CANONICO_OPENAPI if op.implementada],
    ids=lambda op: f"{op.metodo}_{op.operation_id}",
)
def test_rotas_implementadas_expostas_no_openapi_json(
    client: TestClient,
    operacao: OperacaoOpenAPI,
) -> None:
    """Cada operação J1-J3 implementada deve constar em `/openapi.json` com método e 2xx."""
    schema = client.get("/openapi.json").json()
    paths_normalizados = {_normalizar_caminho(p): p for p in schema["paths"]}
    chave_norm = _normalizar_caminho(operacao.caminho)

    assert chave_norm in paths_normalizados, f"Caminho {operacao.caminho} ausente em /openapi.json"
    caminho_fastapi = paths_normalizados[chave_norm]
    metodos = schema["paths"][caminho_fastapi]
    metodo_lower = operacao.metodo.lower()
    assert metodo_lower in metodos, f"Método {operacao.metodo} ausente em {caminho_fastapi}"

    respostas = metodos[metodo_lower]["responses"]
    assert str(operacao.status_sucesso) in respostas, (
        f"Status {operacao.status_sucesso} ausente nas respostas de "
        f"{operacao.metodo} {caminho_fastapi}"
    )


@pytest.mark.parametrize(
    "operacao",
    [op for op in CONTRATO_CANONICO_OPENAPI if not op.implementada],
    ids=lambda op: f"{op.metodo}_{op.operation_id}",
)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rotas J4/J5/Auditoria/Interno do contrato OpenAPI ainda não implementadas nesta branch."
    ),
)
def test_rotas_j4_j5_ausentes_no_openapi_json(
    client: TestClient,
    operacao: OperacaoOpenAPI,
) -> None:
    """Operações J4/J5/Auditoria/Interno deverão constar no `/openapi.json` ao serem entregues."""
    schema = client.get("/openapi.json").json()
    paths_normalizados = {_normalizar_caminho(p): p for p in schema["paths"]}
    chave_norm = _normalizar_caminho(operacao.caminho)
    assert chave_norm in paths_normalizados
    caminho_fastapi = paths_normalizados[chave_norm]
    assert operacao.metodo.lower() in schema["paths"][caminho_fastapi]


# -----------------------------------------------------------------------------
# Chamadas HTTP reais validando payloads e schemas de resposta 2xx
# -----------------------------------------------------------------------------
def test_contrato_schemas_saude_e_sessao(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """Valida schemas 200 de /healthz, /readyz e /me."""
    resp_h = client.get("/healthz")
    assert resp_h.status_code == 200
    assert resp_h.json() == {"status": "ok"}
    assert resp_h.headers.get("x-request-id")

    resp_r = client.get("/readyz")
    assert resp_r.status_code == 200
    assert resp_r.json() == {"status": "ok"}

    resp_me = client.get("/me", headers=headers_admin_alfa)
    assert resp_me.status_code == 200
    perfil = resp_me.json()
    assert set(perfil.keys()) >= {"uid", "tenantId", "papel"}
    assert perfil["papel"] in ("admin_contratante", "analista_sst", "terceirizada", "porteiro")


def test_contrato_schemas_j1_cadastros_e_contratos(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """Valida esquemas 200/201 das 9 operações de J1 (Terceirizadas, Tipos, Contratos)."""
    cnpj = _gerar_cnpj_valido("87654321")
    resp_ter = client.post(
        "/terceirizadas",
        json={"razaoSocial": "Prestadora Contrato QA LTDA", "cnpj": cnpj},
        headers=headers_admin_alfa,
    )
    assert resp_ter.status_code == 201
    ter = resp_ter.json()
    assert set(ter.keys()) == {"id", "razaoSocial", "cnpj", "ativa"}
    assert re.fullmatch(r"\d{14}", ter["cnpj"])
    assert isinstance(ter["ativa"], bool)

    resp_ters = client.get("/terceirizadas", headers=headers_admin_alfa)
    assert resp_ters.status_code == 200
    pag_ters = resp_ters.json()
    assert {"itens", "proximoCursor"}.issubset(pag_ters.keys())
    assert isinstance(pag_ters["itens"], list)

    resp_tipo = client.post(
        "/tipos-documento",
        json={"nome": "ASO Contrato QA", "validadeMeses": 12},
        headers=headers_admin_alfa,
    )
    assert resp_tipo.status_code == 201
    tipo = resp_tipo.json()
    assert set(tipo.keys()) == {"id", "nome", "validadeMeses"}

    resp_tipos = client.get("/tipos-documento", headers=headers_admin_alfa)
    assert resp_tipos.status_code == 200
    assert "itens" in resp_tipos.json()

    resp_ctr = client.post(
        "/contratos",
        json={
            "identificador": "CTR-CONTRATO-QA-01",
            "terceirizadaId": ter["id"],
            "dataInicio": "2026-01-01",
            "dataFim": "2027-12-31",
        },
        headers=headers_admin_alfa,
    )
    assert resp_ctr.status_code == 201
    ctr = resp_ctr.json()
    chaves_contrato = {
        "id",
        "terceirizadaId",
        "identificador",
        "dataInicio",
        "dataFim",
        "vigente",
        "exigencias",
        "versaoMatriz",
    }
    assert set(ctr.keys()) == chaves_contrato
    assert ctr["versaoMatriz"] == 0

    resp_ctrs = client.get("/contratos", headers=headers_admin_alfa)
    assert resp_ctrs.status_code == 200
    assert {"itens", "proximoCursor"}.issubset(resp_ctrs.json().keys())

    resp_det = client.get(f"/contratos/{ctr['id']}", headers=headers_admin_alfa)
    assert resp_det.status_code == 200
    assert resp_det.json()["id"] == ctr["id"]

    resp_ex = client.put(
        f"/contratos/{ctr['id']}/exigencias",
        json={"tipoDocumentoIds": [tipo["id"]]},
        headers=headers_admin_alfa,
    )
    assert resp_ex.status_code == 200
    ctr_atualizado = resp_ex.json()
    assert ctr_atualizado["versaoMatriz"] == 1
    assert len(ctr_atualizado["exigencias"]) == 1
    assert set(ctr_atualizado["exigencias"][0].keys()) == {"id", "nome", "validadeMeses"}


def test_contrato_schemas_j2_j3_trabalhadores_documentos(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """Valida esquemas 200/201 de Trabalhadores, Alocações e Documentos (PDF/JPG e revisão)."""
    cpf = _gerar_cpf_valido("900000101")
    resp_trb = client.post(
        "/trabalhadores",
        json={"nome": "Trabalhador Fictício Contrato", "cpf": cpf, "terceirizadaId": TER_ALFA_1},
        headers=headers_admin_alfa,
    )
    assert resp_trb.status_code == 201
    trb = resp_trb.json()
    assert set(trb.keys()) == {"id", "terceirizadaId", "nome", "cpfMascarado", "ativo"}
    assert cpf not in resp_trb.text
    assert re.fullmatch(r"\*\*\*\.\d{3}\.\d{3}-\*\*", trb["cpfMascarado"])

    resp_trbs = client.get("/trabalhadores", headers=headers_terceirizada_1_alfa)
    assert resp_trbs.status_code == 200
    assert {"itens", "proximoCursor"}.issubset(resp_trbs.json().keys())

    resp_ctr = client.post(
        "/contratos",
        json={
            "identificador": "CTR-CONTRATO-J2-01",
            "terceirizadaId": TER_ALFA_1,
            "dataInicio": "2026-01-01",
            "dataFim": "2027-12-31",
        },
        headers=headers_admin_alfa,
    )
    assert resp_ctr.status_code == 201
    ctr_id = resp_ctr.json()["id"]

    resp_aloc = client.post(
        f"/contratos/{ctr_id}/alocacoes",
        json={"trabalhadorId": trb["id"]},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_aloc.status_code == 201
    aloc = resp_aloc.json()
    assert {
        "contratoId",
        "trabalhadorId",
        "semaforo",
        "pendencias",
        "calculadoEm",
        "proximoVencimento",
    }.issubset(aloc.keys())
    assert aloc["semaforo"] in ("VERDE", "AMARELO", "VERMELHO")

    resp_det_trb = client.get(f"/trabalhadores/{trb['id']}", headers=headers_terceirizada_1_alfa)
    assert resp_det_trb.status_code == 200
    chaves_det_trb = {
        "id",
        "terceirizadaId",
        "nome",
        "cpfMascarado",
        "ativo",
        "documentos",
        "alocacoes",
    }
    assert set(resp_det_trb.json().keys()) == chaves_det_trb

    resp_up_pdf = client.post(
        f"/trabalhadores/{trb['id']}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(PDF_FICTICIO), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31", "dataEmissao": "2026-01-15"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up_pdf.status_code == 201
    doc_pdf = resp_up_pdf.json()
    assert doc_pdf["statusRevisao"] == "PENDENTE"
    assert doc_pdf["arquivo"]["contentType"] == "application/pdf"
    assert "caminho" not in doc_pdf["arquivo"]

    resp_up_jpg = client.post(
        f"/trabalhadores/{trb['id']}/documentos",
        files={"arquivo": ("foto.jpg", io.BytesIO(JPEG_FICTICIO), "image/jpeg")},
        data={"tipoDocumentoId": "tip_nr10", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up_jpg.status_code == 201
    doc_jpg = resp_up_jpg.json()
    assert doc_jpg["arquivo"]["contentType"] == "image/jpeg"

    resp_docs = client.get("/documentos", headers=headers_analista_alfa)
    assert resp_docs.status_code == 200
    assert {"itens", "proximoCursor"}.issubset(resp_docs.json().keys())

    resp_doc_det = client.get(f"/documentos/{doc_pdf['id']}", headers=headers_terceirizada_1_alfa)
    assert resp_doc_det.status_code == 200
    assert resp_doc_det.json()["id"] == doc_pdf["id"]

    resp_url = client.post(
        f"/documentos/{doc_pdf['id']}/url-download",
        headers=headers_analista_alfa,
    )
    assert resp_url.status_code == 200
    url_dados = resp_url.json()
    assert set(url_dados.keys()) == {"url", "expiraEm"}
    assert url_dados["url"].startswith("http")

    resp_aprov = client.post(
        f"/documentos/{doc_pdf['id']}/aprovacao",
        json={"validoAte": "2028-01-15"},
        headers=headers_analista_alfa,
    )
    assert resp_aprov.status_code == 200
    assert resp_aprov.json()["statusRevisao"] == "APROVADO"
    assert resp_aprov.json()["validoAte"] == "2028-01-15"
    assert resp_aprov.json()["revisadoEm"] is not None

    resp_rej = client.post(
        f"/documentos/{doc_jpg['id']}/rejeicao",
        json={"motivo": "Documento ilegível na área de assinatura técnica"},
        headers=headers_analista_alfa,
    )
    assert resp_rej.status_code == 200
    assert resp_rej.json()["statusRevisao"] == "REJEITADO"
    assert resp_rej.json()["motivoRejeicao"] is not None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Divergência de contrato: schema Pendencia retorna 'tipoNome' em "
        "src/habilita/trabalhadores/schemas.py, mas openapi.yaml exige 'tipoDocumentoNome'."
    ),
)
def test_contrato_schema_pendencia_usa_tipo_documento_nome(
    client: TestClient,
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """Alocação com pendência deve expor `tipoDocumentoNome` conforme schema `Pendencia`."""
    resp = client.get(f"/trabalhadores/{TRB_ALFA_1}", headers=headers_terceirizada_1_alfa)
    assert resp.status_code == 200
    alocacoes = resp.json()["alocacoes"]
    assert alocacoes and alocacoes[0]["pendencias"]
    assert "tipoDocumentoNome" in alocacoes[0]["pendencias"][0]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Divergência de contrato: GET /documentos usa query param 'statusRevisao' no FastAPI "
        "em vez de 'status' especificado no openapi.yaml."
    ),
)
def test_contrato_get_documentos_filtra_por_query_param_status(
    client: TestClient,
    headers_analista_alfa: dict[str, str],
) -> None:
    """GET /documentos?status=APROVADO deve filtrar apenas documentos aprovados."""
    resp = client.get("/documentos?status=APROVADO", headers=headers_analista_alfa)
    assert resp.status_code == 200
    itens = resp.json()["itens"]
    assert itens
    assert all(doc["statusRevisao"] == "APROVADO" for doc in itens)


# -----------------------------------------------------------------------------
# Conformidade RFC 9457 (Problem Details) em todos os erros 4xx e 5xx
# -----------------------------------------------------------------------------
def test_conformidade_rfc9457_erros_4xx_e_5xx(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valida `application/problem+json` e campos RFC 9457 em 400..415 e 503."""
    # 400 - Erro de validação de entrada (campo extra proibido)
    resp_400 = client.post(
        "/tipos-documento",
        json={"nome": "Tipo Invalido", "validadeMeses": 12, "tenantId": "tnt_beta"},
        headers=headers_admin_alfa,
    )
    corpo_400 = _assert_problem_details_rfc9457(resp_400, 400, "/tipos-documento")
    assert isinstance(corpo_400.get("erros"), list) and corpo_400["erros"]

    # 401 - Sem autorização
    resp_401 = client.get("/me")
    _assert_problem_details_rfc9457(resp_401, 401, "/me")

    # 403 - Papel sem permissão
    resp_403 = client.post(
        "/tipos-documento",
        json={"nome": "Bloqueado", "validadeMeses": 12},
        headers=headers_terceirizada_1_alfa,
    )
    _assert_problem_details_rfc9457(resp_403, 403, "/tipos-documento")

    # 404 - Recurso inexistente / outro tenant
    resp_404 = client.get(f"/contratos/{CTR_BETA_1}", headers=headers_admin_alfa)
    _assert_problem_details_rfc9457(resp_404, 404, f"/contratos/{CTR_BETA_1}")

    # 405 - Método HTTP não permitido
    resp_405 = client.delete("/healthz")
    _assert_problem_details_rfc9457(resp_405, 405, "/healthz")

    # 409 - Conflito (aprovar documento já aprovado)
    resp_409 = client.post(f"/documentos/{DOC_ALFA_1_ASO}/aprovacao", headers=headers_analista_alfa)
    _assert_problem_details_rfc9457(resp_409, 409, f"/documentos/{DOC_ALFA_1_ASO}/aprovacao")

    # 413 - Payload maior que 5.000.000 bytes
    amanha = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
    payload_grande = b"%PDF" + b"0" * 5_000_000
    resp_413 = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("grande.pdf", io.BytesIO(payload_grande), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": amanha},
        headers=headers_terceirizada_1_alfa,
    )
    _assert_problem_details_rfc9457(resp_413, 413, f"/trabalhadores/{TRB_ALFA_1}/documentos")

    # 415 - Assinatura mágica inválida
    resp_415 = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("falso.pdf", io.BytesIO(b"MZ\x90\x00NAO_E_PDF"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": amanha},
        headers=headers_terceirizada_1_alfa,
    )
    _assert_problem_details_rfc9457(resp_415, 415, f"/trabalhadores/{TRB_ALFA_1}/documentos")

    # 503 - Indisponibilidade do Firestore no /readyz
    mock_db = MagicMock()
    mock_db.collection.return_value.limit.return_value.stream.side_effect = TimeoutError(
        "Timeout Firestore"
    )
    monkeypatch.setattr("habilita.saude.rotas.obter_firestore_client", lambda: mock_db)
    resp_503 = client.get("/readyz")
    _assert_problem_details_rfc9457(resp_503, 503, "/readyz")
    assert "CTR_ALFA_1" not in resp_503.text and CTR_ALFA_1 not in resp_503.text
