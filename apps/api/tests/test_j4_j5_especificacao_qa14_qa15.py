"""Especificação executável das Jornadas J4 e J5 ([QA-14] #89 e [QA-15] #90).

Todos os cenários estão marcados com `@pytest.mark.xfail(strict=True, ...)`
pois aguardam a implementação das rotas da Semana 4:
- J4 / [QA-14] #89: POST /internal/jobs/recalculo-vencimentos (H15 / ADR 0007 / ADR 0010)
- J5 / [QA-15] #90: POST /liberacoes/consultas (H04 / ADR 0008 / RN-11)
"""

from __future__ import annotations

import datetime
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import CTR_ALFA_1, TER_ALFA_1

UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017
PDF_FICTICIO = (
    b"%PDF-1.4\n" + "DOCUMENTO FICTÍCIO — TESTE AUTOMATIZADO HABILITA\n".encode() + b"0" * 64
)


def _gerar_cpf_ficticio(semente: int) -> str:
    """Gera CPF matemático fictício válido (sem PII real)."""
    base = f"{920000000 + semente:09d}"
    d1 = sum(int(base[i]) * (10 - i) for i in range(9)) % 11
    d1 = 0 if d1 < 2 else 11 - d1
    b10 = base + str(d1)
    d2 = sum(int(b10[i]) * (11 - i) for i in range(10)) % 11
    d2 = 0 if d2 < 2 else 11 - d2
    return f"{base}{d1}{d2}"


# =============================================================================
# [QA-14] #89 — Job diário de recálculo de vencimentos e alertas (J4 / H15)
# =============================================================================
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Rota POST /internal/jobs/recalculo-vencimentos (J4 / H15) ainda não implementada.",
)
def test_qa14_job_recalculo_exige_autenticacao_segredo_interno(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-14 (H15 / ADR 0007): aceita apenas Bearer <JOB_TOKEN>; sem segredo ou Firebase -> 401."""
    monkeypatch.setenv("JOB_TOKEN", "teste-teste-teste-aaaa")

    # 1. Sem cabeçalho Authorization -> 401 RFC 9457
    resp_sem_auth = client.post("/internal/jobs/recalculo-vencimentos", json={})
    assert resp_sem_auth.status_code == 401
    assert "application/problem+json" in resp_sem_auth.headers.get("content-type", "")
    assert resp_sem_auth.json().get("status") == 401

    # 2. Segredo incorreto -> 401 RFC 9457
    resp_segredo_invalido = client.post(
        "/internal/jobs/recalculo-vencimentos",
        json={},
        headers={"Authorization": "Bearer segredo-errado"},
    )
    assert resp_segredo_invalido.status_code == 401
    assert "application/problem+json" in resp_segredo_invalido.headers.get("content-type", "")

    # 3. Token de usuário Firebase (mesmo admin_contratante) -> 401 (rota exclusiva do job)
    resp_firebase = client.post(
        "/internal/jobs/recalculo-vencimentos",
        json={},
        headers=headers_admin_alfa,
    )
    assert resp_firebase.status_code == 401
    assert "application/problem+json" in resp_firebase.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Rota POST /internal/jobs/recalculo-vencimentos (J4 / H15) ainda não implementada.",
)
def test_qa14_job_recalculo_multiplas_execucoes_mesmo_dia_nao_duplica_alertas(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-14 (H15 / RN-12): reexecutar o job no mesmo dia é idempotente e não duplica alertas."""
    segredo_job = "teste-teste-teste-aaaa"
    monkeypatch.setenv("JOB_TOKEN", segredo_job)
    headers_job = {"Authorization": f"Bearer {segredo_job}"}

    hoje = datetime.datetime.now(UTC).date()
    valido_em_15_dias = (hoje + datetime.timedelta(days=15)).isoformat()
    data_referencia = hoje.isoformat()

    # Prepara trabalhador e documento aprovado prestes a vencer (<= 30 dias)
    resp_trb = client.post(
        "/trabalhadores",
        json={
            "nome": "Trab Alerta Idempotente QA14",
            "cpf": _gerar_cpf_ficticio(1401),
            "terceirizadaId": TER_ALFA_1,
        },
        headers=headers_admin_alfa,
    )
    assert resp_trb.status_code == 201
    trb_id = resp_trb.json()["id"]

    resp_up = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso_qa14.pdf", io.BytesIO(PDF_FICTICIO), "application/pdf")},
        data={
            "tipoDocumentoId": "tip_aso",
            "dataEmissao": (hoje - datetime.timedelta(days=30)).isoformat(),
            "validoAte": valido_em_15_dias,
        },
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    resp_aprov = client.post(f"/documentos/{doc_id}/aprovacao", headers=headers_analista_alfa)
    assert resp_aprov.status_code == 200

    # 1a execução do job no dia -> cria alerta determinístico {documentoId}_VENCE_EM_30_DIAS
    resp_job_1 = client.post(
        "/internal/jobs/recalculo-vencimentos",
        json={"dataReferencia": data_referencia},
        headers=headers_job,
    )
    assert resp_job_1.status_code == 200
    exec_1: dict[str, Any] = resp_job_1.json()
    assert exec_1["dataReferencia"] == data_referencia
    assert exec_1["status"] == "CONCLUIDO"
    assert exec_1["alertasCriados"] >= 1

    # 2a e 3a execuções no mesmo dia -> idempotência: zero alertas duplicados
    for _ in range(2):
        resp_job_re = client.post(
            "/internal/jobs/recalculo-vencimentos",
            json={"dataReferencia": data_referencia},
            headers=headers_job,
        )
        assert resp_job_re.status_code == 200
        exec_re: dict[str, Any] = resp_job_re.json()
        assert exec_re["status"] == "CONCLUIDO"
        assert exec_re["alertasCriados"] == 0

    # Confirma via GET /alertas que há exatamente um alerta para (doc_id, VENCE_EM_30_DIAS)
    resp_alertas = client.get("/alertas", headers=headers_analista_alfa)
    assert resp_alertas.status_code == 200
    itens: list[dict[str, Any]] = resp_alertas.json().get("itens", [])
    alertas_doc = [
        a for a in itens if a.get("documentoId") == doc_id and a.get("tipo") == "VENCE_EM_30_DIAS"
    ]
    assert len(alertas_doc) == 1
    assert alertas_doc[0]["id"] == f"{doc_id}_VENCE_EM_30_DIAS"


# =============================================================================
# [QA-15] #90 — Consulta na portaria sem vazamento de dados (J5 / H04)
# =============================================================================
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Rota POST /liberacoes/consultas (J5 / H04 / ADR 0008) ainda não implementada.",
)
def test_qa15_porteiro_consulta_bloqueado_sem_vazar_documentos_urls_ou_motivos(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
    headers_porteiro_alfa: dict[str, str],
) -> None:
    """QA-15 (H04 / ADR 0008): portaria recebe BLOQUEADO sem vazar documentos, URLs ou motivos."""
    cpf_ficticio = _gerar_cpf_ficticio(1501)
    motivo_sentinela = "MOTIVO_CONFIDENCIAL_ASO_RASURADO_TESTE_QA15"

    # 1. Cadastra trabalhador, aloca em contrato vigente (fica VERMELHO) e rejeita um documento
    resp_trb = client.post(
        "/trabalhadores",
        json={
            "nome": "Trabalhador Bloqueado QA15",
            "cpf": cpf_ficticio,
            "terceirizadaId": TER_ALFA_1,
        },
        headers=headers_admin_alfa,
    )
    assert resp_trb.status_code == 201
    trb_id = resp_trb.json()["id"]

    resp_aloc = client.post(
        f"/contratos/{CTR_ALFA_1}/alocacoes",
        json={"trabalhadorId": trb_id},
        headers=headers_admin_alfa,
    )
    assert resp_aloc.status_code == 201
    assert resp_aloc.json()["semaforo"] == "VERMELHO"

    resp_up = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso_qa15.pdf", io.BytesIO(PDF_FICTICIO), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    resp_rej = client.post(
        f"/documentos/{doc_id}/rejeicao",
        json={"motivo": motivo_sentinela},
        headers=headers_analista_alfa,
    )
    assert resp_rej.status_code == 200

    # 2. Porteiro consulta liberação por CPF (nunca na URL)
    resp_consulta = client.post(
        "/liberacoes/consultas",
        json={"cpf": cpf_ficticio},
        headers=headers_porteiro_alfa,
    )
    assert resp_consulta.status_code == 200
    dados: dict[str, Any] = resp_consulta.json()

    # Schema estrito ResultadoLiberacao (additionalProperties: false)
    assert set(dados.keys()) <= {
        "consultaId",
        "resultado",
        "trabalhador",
        "orientacao",
        "expiraEm",
    }
    assert {"consultaId", "resultado", "orientacao", "expiraEm"}.issubset(dados.keys())
    assert dados["resultado"] == "BLOQUEADO"
    assert isinstance(dados["orientacao"], str) and dados["orientacao"].strip()

    trabalhador_resumo = dados.get("trabalhador", {})
    assert set(trabalhador_resumo.keys()) == {"id", "nome", "cpfMascarado"}
    assert trabalhad_id_esperado(trabalhador_resumo, trb_id)

    # Garantia explícita contra vazamentos de documentos, storage URLs, motivos ou CPF bruto
    campos_proibidos = {
        "documentos",
        "pendencias",
        "semaforo",
        "motivoRejeicao",
        "url",
        "caminho",
        "storage",
        "arquivo",
        "contratoId",
        "contratos",
        "tiposDocumento",
        "validoAte",
    }
    assert campos_proibidos.isdisjoint(dados.keys())

    texto_bruto = resp_consulta.text
    for segredo in (
        motivo_sentinela,
        cpf_ficticio,
        "http://",
        "https://",
        "mock-signed",
        "/object/sign/",
        "tenants/",
        "tip_aso",
        "tip_nr10",
        "tip_nr35",
        doc_id,
    ):
        assert segredo not in texto_bruto


def trabalhad_id_esperado(trabalhador_resumo: dict[str, Any], trb_id: str) -> bool:
    return trabalhador_resumo.get("id") == trb_id


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Rota POST /liberacoes/consultas (J5 / H04 / ADR 0008) ainda não implementada.",
)
def test_qa15_porteiro_consulta_cpf_nao_cadastrado_sem_vazar_metadados(
    client: TestClient,
    headers_porteiro_alfa: dict[str, str],
) -> None:
    """QA-15 (H04): CPF inexistente retorna NAO_CADASTRADO sem expor campo trabalhador."""
    cpf_desconhecido = _gerar_cpf_ficticio(1599)
    resp = client.post(
        "/liberacoes/consultas",
        json={"cpf": cpf_desconhecido},
        headers=headers_porteiro_alfa,
    )
    assert resp.status_code == 200
    dados: dict[str, Any] = resp.json()
    assert dados.get("resultado") == "NAO_CADASTRADO"
    assert "trabalhador" not in dados
    assert cpf_desconhecido not in resp.text
