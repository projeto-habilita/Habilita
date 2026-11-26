"""Testes dos critérios apontados na revisão do PR #32 (pontos 1 a 5 e 6 parcial).

- H07.CA4: token válido sem claims -> 403 (não 401), log sem dados pessoais
- H01.CA2 / H02.CA2 / H03.CA2 / H03.CA3: validação de entrada -> 400
- H03.CA2: motivo de rejeição só com espaços é recusado
- H02.CA8: dataEmissao no futuro -> 400
- H02.CA10: validoAte anterior à dataEmissao -> 400
- H01.CA6: alterar a matriz grava versão anterior e nova na auditoria
- Download: validade da URL limitada a 300 s
"""

from __future__ import annotations

import datetime
import io
import logging

import pytest
from fastapi.testclient import TestClient

from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.comum.auth import extrair_usuario
from habilita.comum.erros import AppException
from habilita.trabalhadores.repositorio import TrabalhadoresRepositorio

from .conftest import CTR_ALFA_1, DOC_ALFA_1_ASO, TNT_ALFA, TRB_ALFA_1

PDF = b"%PDF-1.4 teste de criterio"


def _upload(
    client: TestClient, headers: dict[str, str], valido_ate: str, data_emissao: str | None
) -> int:
    dados = {"tipoDocumentoId": "tip_aso", "validoAte": valido_ate}
    if data_emissao is not None:
        dados["dataEmissao"] = data_emissao
    resp = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("doc.pdf", io.BytesIO(PDF), "application/pdf")},
        data=dados,
        headers=headers,
    )
    return resp.status_code


# -----------------------------------------------------------------------------
# H07.CA4 - claims ausentes
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "claims",
    [
        {"uid": "u1", "papel": "admin_contratante"},  # sem tenantId
        {"uid": "u1", "tenantId": "tnt_alfa"},  # sem papel
        {"uid": "u1"},  # sem nenhuma
    ],
)
def test_h07_ca4_token_sem_claims_retorna_403_e_log_sem_pii(
    claims: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("firebase_admin.auth.verify_id_token", lambda _token: claims)
    with caplog.at_level(logging.WARNING), pytest.raises(AppException) as exc_info:
        extrair_usuario(authorization="Bearer qualquer")
    assert exc_info.value.status_code == 403
    registros = [r for r in caplog.records if getattr(r, "evento", "") == "claims_ausentes"]
    assert registros, "evento claims_ausentes não foi registrado"
    assert "u1" not in caplog.text


def test_h07_ca4_token_invalido_continua_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falha(_token: str) -> dict[str, str]:
        raise ValueError("token inválido")

    monkeypatch.setattr("firebase_admin.auth.verify_id_token", _falha)
    with pytest.raises(AppException) as exc_info:
        extrair_usuario(authorization="Bearer qualquer")
    assert exc_info.value.status_code == 401


# -----------------------------------------------------------------------------
# Validação de entrada -> 400
# -----------------------------------------------------------------------------
def test_h01_ca2_payload_invalido_retorna_400_problem_details(
    client: TestClient, headers_admin_alfa: dict[str, str]
) -> None:
    resp = client.post("/terceirizadas", json={"razaoSocial": ""}, headers=headers_admin_alfa)
    assert resp.status_code == 400
    assert "application/problem+json" in resp.headers.get("content-type", "")
    assert resp.json()["status"] == 400


# -----------------------------------------------------------------------------
# H03.CA2 - motivo só com espaços
# -----------------------------------------------------------------------------
def test_h03_ca2_motivo_so_com_espacos_retorna_400(
    client: TestClient,
    headers_analista_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    resp_doc = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("doc.pdf", io.BytesIO(PDF), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    doc_id = resp_doc.json()["id"]
    resp = client.post(
        f"/documentos/{doc_id}/rejeicao",
        json={"motivo": "               "},
        headers=headers_analista_alfa,
    )
    assert resp.status_code == 400


# -----------------------------------------------------------------------------
# H02.CA8 / H02.CA10 - datas do upload
# -----------------------------------------------------------------------------
def test_h02_ca8_data_emissao_no_futuro_retorna_400(
    client: TestClient, headers_terceirizada_1_alfa: dict[str, str]
) -> None:
    futuro = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    assert _upload(client, headers_terceirizada_1_alfa, "2099-12-31", futuro) == 400


def test_h02_ca10_valido_ate_anterior_a_emissao_retorna_400(
    client: TestClient, headers_terceirizada_1_alfa: dict[str, str]
) -> None:
    assert _upload(client, headers_terceirizada_1_alfa, "2026-01-01", "2026-06-01") == 400


def test_h02_upload_data_em_formato_invalido_retorna_400(
    client: TestClient, headers_terceirizada_1_alfa: dict[str, str]
) -> None:
    assert _upload(client, headers_terceirizada_1_alfa, "31/12/2027", None) == 400


def test_h02_upload_datas_validas_continua_201(
    client: TestClient, headers_terceirizada_1_alfa: dict[str, str]
) -> None:
    assert _upload(client, headers_terceirizada_1_alfa, "2027-06-30", "2026-06-30") == 201


# -----------------------------------------------------------------------------
# H01.CA6 - auditoria da matriz
# -----------------------------------------------------------------------------
def test_h01_ca6_alterar_matriz_registra_versao_anterior_e_nova_na_auditoria(
    client: TestClient, headers_admin_alfa: dict[str, str]
) -> None:
    repo = CadastrosRepositorio(tenant_id=TNT_ALFA)
    antes = len(repo.listar_auditoria())

    resp = client.put(
        f"/contratos/{CTR_ALFA_1}/exigencias",
        json={"tipoDocumentoIds": ["tip_aso"]},
        headers=headers_admin_alfa,
    )
    assert resp.status_code == 200
    versao_nova = resp.json()["versaoMatriz"]

    eventos = repo.listar_auditoria()
    assert len(eventos) == antes + 1
    ev = eventos[-1]
    assert ev["tipo"] == "MATRIZ_EXIGENCIAS_ALTERADA"
    assert ev["contratoId"] == CTR_ALFA_1
    assert ev["versaoNova"] == versao_nova
    assert ev["versaoAnterior"] == versao_nova - 1
    assert ev["exigenciasNovas"] == ["tip_aso"]
    assert ev["atorUid"] == "uid_admin_alfa"
    assert "exigenciasAnteriores" in ev


# -----------------------------------------------------------------------------
# Download e Storage (Ponto 6: porta ArmazenamentoDocumentos e teto de 300 s)
# -----------------------------------------------------------------------------
def test_url_download_expira_em_no_maximo_300_segundos() -> None:
    repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA)
    antes = datetime.datetime.now(datetime.UTC)
    resp = repo.gerar_url_download(DOC_ALFA_1_ASO)
    assert resp is not None
    assert "9199" not in resp.url
    expira = datetime.datetime.fromisoformat(resp.expiraEm)
    assert (expira - antes).total_seconds() <= 301


def test_h12_ca3_upload_grava_na_porta_armazenamento_e_gera_url_assinada_300s() -> None:
    """Valida gravação via ArmazenamentoDocumentos e URL assinada de 300s."""
    from habilita.trabalhadores.schemas import ArquivoMetadados

    from .memoria_armazenamento import MemoriaArmazenamento

    storage_fake = MemoriaArmazenamento(tenant_id=TNT_ALFA, public_url="http://127.0.0.1:5000")
    repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, storage=storage_fake)
    conteudo = b"%PDF-1.4 documento persistido via porta"
    meta = ArquivoMetadados(
        contentType="application/pdf",
        tamanhoBytes=len(conteudo),
        sha256="a" * 64,
    )

    doc = repo.criar_documento(
        trabalhador_id=TRB_ALFA_1,
        tipo_documento_id="tip_aso",
        valido_ate="2027-12-31",
        data_emissao="2026-06-01",
        arquivo_meta=meta,
        conteudo=conteudo,
    )

    resp_url = repo.gerar_url_download(doc.id)
    assert resp_url is not None
    prefixo = (
        f"http://127.0.0.1:5000/mock-signed/tenants/{TNT_ALFA}/"
        f"trabalhadores/{TRB_ALFA_1}/documentos/{doc.id}/"
    )
    assert resp_url.url.startswith(prefixo)
    assert resp_url.url.endswith(".pdf?exp=300")


# -----------------------------------------------------------------------------
# H01.CA3 / H08.CA3 / H10.CA3 / H13.CA3 - lacunas da suíte complementar (#102)
# -----------------------------------------------------------------------------
def test_h01_ca3_contrato_com_data_fim_no_passado_nao_fica_vigente(
    client: TestClient, headers_admin_alfa: dict[str, str]
) -> None:
    """H01.CA3 (RN-10): contrato com dataFim no passado nasce com vigente=False."""
    from .conftest import TER_ALFA_1

    resp = client.post(
        "/contratos",
        json={
            "identificador": "CTR-EXPIRADO-2025",
            "terceirizadaId": TER_ALFA_1,
            "dataInicio": "2025-01-01",
            "dataFim": "2025-12-31",
        },
        headers=headers_admin_alfa,
    )
    assert resp.status_code == 201
    assert resp.json()["vigente"] is False


def test_h08_ca3_cnpj_duplicado_no_mesmo_tenant_retorna_409(
    client: TestClient, headers_admin_alfa: dict[str, str]
) -> None:
    """H08.CA3: cadastro de terceirizada com CNPJ já existente no tenant retorna 409."""
    payload = {
        "razaoSocial": "Terceirizada Duplicada LTDA",
        "cnpj": "11.444.777/0001-61",
    }
    resp_1 = client.post("/terceirizadas", json=payload, headers=headers_admin_alfa)
    assert resp_1.status_code == 201

    resp_2 = client.post("/terceirizadas", json=payload, headers=headers_admin_alfa)
    assert resp_2.status_code == 409
    assert "application/problem+json" in resp_2.headers.get("content-type", "")


def test_h10_ca3_vincular_trabalhador_duplicado_no_mesmo_contrato_retorna_409(
    client: TestClient, headers_admin_alfa: dict[str, str]
) -> None:
    """H10.CA3: vincular o mesmo trabalhador duas vezes no mesmo contrato retorna 409."""
    from .conftest import TER_ALFA_1

    resp_ctr = client.post(
        "/contratos",
        json={
            "identificador": "CTR-ALOC-DUP-PR32",
            "terceirizadaId": TER_ALFA_1,
            "dataInicio": "2026-01-01",
            "dataFim": "2027-12-31",
        },
        headers=headers_admin_alfa,
    )
    assert resp_ctr.status_code == 201
    ctr_id = resp_ctr.json()["id"]

    resp_1 = client.post(
        f"/contratos/{ctr_id}/alocacoes",
        json={"trabalhadorId": TRB_ALFA_1},
        headers=headers_admin_alfa,
    )
    assert resp_1.status_code == 201

    resp_2 = client.post(
        f"/contratos/{ctr_id}/alocacoes",
        json={"trabalhadorId": TRB_ALFA_1},
        headers=headers_admin_alfa,
    )
    assert resp_2.status_code == 409
    assert "application/problem+json" in resp_2.headers.get("content-type", "")


def test_h13_ca3_aprovar_documento_com_valido_ate_anterior_data_emissao_retorna_400(
    client: TestClient,
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H13.CA3 (RN-09): aprovar corrigindo validoAte < dataEmissao retorna 400."""
    resp_up = client.post(
        f"/trabalhadores/{TRB_ALFA_1}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(PDF), "application/pdf")},
        data={
            "tipoDocumentoId": "tip_aso",
            "dataEmissao": "2026-06-10",
            "validoAte": "2027-06-10",
        },
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    # 1. Override de validoAte na aprovação para data anterior à emissão -> 400
    resp_err = client.post(
        f"/documentos/{doc_id}/aprovacao",
        json={"validoAte": "2026-06-01"},
        headers=headers_analista_alfa,
    )
    assert resp_err.status_code == 400
    assert "application/problem+json" in resp_err.headers.get("content-type", "")

    # 2. Override válido na aprovação -> 200 e atualiza validoAte
    resp_ok = client.post(
        f"/documentos/{doc_id}/aprovacao",
        json={"validoAte": "2027-12-31"},
        headers=headers_analista_alfa,
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["statusRevisao"] == "APROVADO"
    assert resp_ok.json()["validoAte"] == "2027-12-31"
