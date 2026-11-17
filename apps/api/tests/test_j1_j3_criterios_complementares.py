"""Testes complementares das jornadas J1, J2 e J3 (critérios sem cobertura no PR #32).

Cobre todos os critérios de aceite de J1 (H01, H08, H09), J2 (H02, H10, H11)
e J3 (H03, H12, H13, H14) que ainda não tinham teste dedicado ou tinham cobertura
apenas parcial em `test_j1_contratos_cadastros.py` e
`test_j2_j3_trabalhadores_documentos.py`.

Testes marcados com `@pytest.mark.xfail(strict=True)` documentam lacunas reais
identificadas na implementação atual do PR #32.
"""

from __future__ import annotations

import datetime
import io

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    CTR_ALFA_1,
    DOC_ALFA_1_ASO,
    TER_ALFA_1,
    TER_BETA_1,
    TRB_ALFA_Y1,
)

UTC = datetime.UTC


def _gerar_cpf_valido(semente: int) -> str:
    """Gera um CPF fictício matematicamente válido a partir de um inteiro."""
    base = f"{200_000_000 + semente:09d}"
    soma1 = sum(int(base[i]) * (10 - i) for i in range(9))
    r1 = soma1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    parcial = base + str(d1)
    soma2 = sum(int(parcial[i]) * (11 - i) for i in range(10))
    r2 = soma2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return parcial + str(d2)


def _criar_trabalhador_alfa(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    nome: str,
    semente_cpf: int,
    terceirizada_id: str = TER_ALFA_1,
) -> str:
    resp = client.post(
        "/trabalhadores",
        json={
            "nome": nome,
            "cpf": _gerar_cpf_valido(semente_cpf),
            "terceirizadaId": terceirizada_id,
        },
        headers=headers_admin_alfa,
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _criar_contrato_alfa(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    identificador: str,
    terceirizada_id: str = TER_ALFA_1,
    data_inicio: str = "2026-01-01",
    data_fim: str = "2027-12-31",
) -> str:
    resp = client.post(
        "/contratos",
        json={
            "identificador": identificador,
            "terceirizadaId": terceirizada_id,
            "dataInicio": data_inicio,
            "dataFim": data_fim,
        },
        headers=headers_admin_alfa,
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


# =============================================================================
# Jornada J1 — Contratos, Matriz, Terceirizadas e Tipos de Documento
# =============================================================================


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: criar_contrato fixa vigente=True mesmo quando dataFim "
        "está no passado (H01.CA1 / H01.CA3)."
    ),
)
def test_h01_ca3_contrato_com_data_fim_no_passado_nao_fica_vigente(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H01.CA3 (T-J1-01): Contrato com dataFim no passado não deve ser marcado como vigente."""
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
    ctr = resp.json()
    assert ctr["vigente"] is False


def test_h01_ca5_substituir_matriz_tipo_inexistente_ou_outro_tenant_e_limites_1_a_20(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_admin_beta: dict[str, str],
) -> None:
    """H01.CA5 (T-J1-02/03): Matriz exige 1 a 20 tipos válidos do próprio tenant."""
    ctr_id = _criar_contrato_alfa(client, headers_admin_alfa, "CTR-MATRIZ-LIMITES-01")

    # Tipo criado exclusivamente no tenant Beta
    resp_tipo_beta = client.post(
        "/tipos-documento",
        json={"nome": "Certificado Exclusivo Beta", "validadeMeses": 12},
        headers=headers_admin_beta,
    )
    assert resp_tipo_beta.status_code == 201
    tipo_beta_id = resp_tipo_beta.json()["id"]

    # 1. Tipo pertencente a outro tenant deve ser recusado (400/404)
    resp_outro_tenant = client.put(
        f"/contratos/{ctr_id}/exigencias",
        json={"tipoDocumentoIds": [tipo_beta_id]},
        headers=headers_admin_alfa,
    )
    assert resp_outro_tenant.status_code in (400, 404)
    assert "application/problem+json" in resp_outro_tenant.headers.get("content-type", "")

    # 2. Tipo inexistente deve ser recusado (400/404)
    resp_inexistente = client.put(
        f"/contratos/{ctr_id}/exigencias",
        json={"tipoDocumentoIds": ["tip_nao_existe_999"]},
        headers=headers_admin_alfa,
    )
    assert resp_inexistente.status_code in (400, 404)
    assert "application/problem+json" in resp_inexistente.headers.get("content-type", "")

    # 3. Lista vazia (0 itens < min_length=1) deve retornar 422
    resp_vazia = client.put(
        f"/contratos/{ctr_id}/exigencias",
        json={"tipoDocumentoIds": []},
        headers=headers_admin_alfa,
    )
    assert resp_vazia.status_code == 422
    assert "application/problem+json" in resp_vazia.headers.get("content-type", "")

    # 4. Lista com 21 itens (> max_length=20) deve retornar 422
    lista_21 = [f"tip_extra_{i}" for i in range(21)]
    resp_21 = client.put(
        f"/contratos/{ctr_id}/exigencias",
        json={"tipoDocumentoIds": lista_21},
        headers=headers_admin_alfa,
    )
    assert resp_21.status_code == 422
    assert "application/problem+json" in resp_21.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Trilha de auditoria (H01.CA6 / H20) ainda não implementada no PR #32.",
)
def test_h01_ca6_alteracao_matriz_registra_auditoria(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H01.CA6 (T-J1-04): Alteração na matriz gera registro na trilha de auditoria."""
    ctr_id = _criar_contrato_alfa(client, headers_admin_alfa, "CTR-AUDITORIA-MATRIZ")
    resp_put = client.put(
        f"/contratos/{ctr_id}/exigencias",
        json={"tipoDocumentoIds": ["tip_aso"]},
        headers=headers_admin_alfa,
    )
    assert resp_put.status_code == 200

    resp_aud = client.get(f"/auditoria?recurso={ctr_id}", headers=headers_admin_alfa)
    assert resp_aud.status_code == 200
    itens = resp_aud.json().get("itens", [])
    assert len(itens) >= 1


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: criar_terceirizada ainda não valida unicidade de CNPJ "
        "no tenant (esperado 409 conforme H08.CA3)."
    ),
)
def test_h08_ca3_cnpj_duplicado_no_mesmo_tenant_retorna_409(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H08.CA3 (T-J1-05): Cadastro de terceirizada com CNPJ já existente retorna 409."""
    payload = {
        "razaoSocial": "Terceirizada Duplicada LTDA",
        "cnpj": "11.222.333/0001-81",
    }
    resp_1 = client.post("/terceirizadas", json=payload, headers=headers_admin_alfa)
    assert resp_1.status_code == 201

    resp_2 = client.post("/terceirizadas", json=payload, headers=headers_admin_alfa)
    assert resp_2.status_code == 409


def test_h09_ca2_tipo_documento_validade_borda_0_e_121_meses_retorna_422(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H09.CA1/CA2 (T-J1-06/07): Validade 0 ou 121 falha (422); bordas 1 e 120 passam (201)."""
    for validade_invalida in (0, 121):
        resp_err = client.post(
            "/tipos-documento",
            json={"nome": f"Doc Invalido {validade_invalida}", "validadeMeses": validade_invalida},
            headers=headers_admin_alfa,
        )
        assert resp_err.status_code == 422
        assert "application/problem+json" in resp_err.headers.get("content-type", "")

    for validade_valida in (1, 120):
        resp_ok = client.post(
            "/tipos-documento",
            json={"nome": f"Doc Borda {validade_valida}m", "validadeMeses": validade_valida},
            headers=headers_admin_alfa,
        )
        assert resp_ok.status_code == 201
        assert resp_ok.json()["validadeMeses"] == validade_valida


# =============================================================================
# Jornada J2 — Trabalhadores, Upload, Alocação e Reuso (H02, H10, H11)
# =============================================================================


def test_h02_ca2_t_cpf_04_05_cpf_sequencia_repetida_e_tamanho_invalido_retorna_422(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H02.CA2 (T-CPF-04, T-CPF-05): Recusa CPF com dígitos repetidos e 10/12 dígitos."""
    for cpf_invalido in ("111.111.111-11", "1234567890", "123456789012"):
        resp = client.post(
            "/trabalhadores",
            json={
                "nome": "Trabalhador CPF Borda",
                "cpf": cpf_invalido,
                "terceirizadaId": TER_ALFA_1,
            },
            headers=headers_admin_alfa,
        )
        assert resp.status_code == 422
        assert "application/problem+json" in resp.headers.get("content-type", "")


def test_h02_ca5_t_upl_02_11_upload_jpeg_e_pdf_no_limite_exato_5mb_retorna_201(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """H02.CA5 (T-UPL-02, T-UPL-11): Aceita JPEG válido e PDF de 4.999.999 e 5.000.000 bytes."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Limite Upload", 101)

    # 1. JPEG válido por assinatura mágica
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 128
    resp_jpg = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("exame.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_jpg.status_code == 201
    doc_jpg = resp_jpg.json()
    assert doc_jpg["statusRevisao"] == "PENDENTE"
    assert doc_jpg["arquivo"]["contentType"] == "image/jpeg"

    # 2. PDF com 4.999.999 bytes (T-UPL-11) e 5.000.000 bytes (T-UPL-02)
    for tamanho in (4_999_999, 5_000_000):
        pdf_bytes = b"%PDF-1.4" + b"0" * (tamanho - 8)
        resp_pdf = client.post(
            f"/trabalhadores/{trb_id}/documentos",
            files={"arquivo": (f"aso_{tamanho}.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
            headers=headers_terceirizada_1_alfa,
        )
        assert resp_pdf.status_code == 201
        doc_pdf = resp_pdf.json()
        assert doc_pdf["statusRevisao"] == "PENDENTE"
        assert doc_pdf["arquivo"]["tamanhoBytes"] == tamanho


def test_h02_ca7_t_upl_05_06_upload_png_disfarcado_de_pdf_e_arquivo_vazio(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """H02.CA7 (T-UPL-05, T-UPL-06): PNG disfarçado de PDF -> 415; arquivo 0 bytes -> 400."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Arquivo Invalido", 102)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    resp_png = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("falso.pdf", io.BytesIO(png_bytes), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_png.status_code == 415
    assert "application/problem+json" in resp_png.headers.get("content-type", "")

    resp_vazio = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("vazio.pdf", io.BytesIO(b""), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_vazio.status_code == 400
    assert "application/problem+json" in resp_vazio.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: enviar_documento ainda não rejeita dataEmissao no futuro (H02.CA8 / T-UPL-07)."
    ),
)
def test_h02_ca8_t_upl_07_upload_data_emissao_no_futuro_rejeitado(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """H02.CA8 (T-UPL-07): Upload com dataEmissao futura deve ser rejeitado (400/422)."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Emissao Futura", 103)
    hoje = datetime.datetime.now(UTC).date()
    amanha = (hoje + datetime.timedelta(days=1)).isoformat()
    valido_ate = (hoje + datetime.timedelta(days=365)).isoformat()

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 futuro"), "application/pdf")},
        data={
            "tipoDocumentoId": "tip_aso",
            "dataEmissao": amanha,
            "validoAte": valido_ate,
        },
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code in (400, 422)


def test_h02_ca10_t_upl_08_upload_sem_valido_ate_retorna_422(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """H02.CA10 (T-UPL-08): Upload sem campo validoAte retorna 422 Problem Details."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Sem Validade", 104)

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 sem validade"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code == 422
    assert "application/problem+json" in resp.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: enviar_documento ainda não rejeita validoAte anterior "
        "a dataEmissao (H02.CA10 / T-UPL-09)."
    ),
)
def test_h02_ca10_t_upl_09_upload_valido_ate_anterior_data_emissao_rejeitado(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """H02.CA10 (T-UPL-09): validoAte anterior a dataEmissao deve ser rejeitado (400/422)."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Validade Invertida", 105)

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 datas invertidas"), "application/pdf")},
        data={
            "tipoDocumentoId": "tip_aso",
            "dataEmissao": "2026-06-10",
            "validoAte": "2026-06-01",
        },
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code in (400, 422)


def test_h10_ca2_vincular_trabalhador_em_contrato_de_outra_terceirizada_retorna_404(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_2_alfa: dict[str, str],
) -> None:
    """H10.CA2 (T-J2-04): Terceirizada 2 tentando alocar em contrato da Terceirizada 1 -> 404."""
    resp_terc2 = client.post(
        f"/contratos/{CTR_ALFA_1}/alocacoes",
        json={"trabalhadorId": TRB_ALFA_Y1},
        headers=headers_terceirizada_2_alfa,
    )
    assert resp_terc2.status_code == 404
    assert "application/problem+json" in resp_terc2.headers.get("content-type", "")

    # Admin tentando alocar trabalhador da Terceirizada 2 em contrato da Terceirizada 1 -> 400
    resp_admin = client.post(
        f"/contratos/{CTR_ALFA_1}/alocacoes",
        json={"trabalhadorId": TRB_ALFA_Y1},
        headers=headers_admin_alfa,
    )
    assert resp_admin.status_code == 400
    assert "application/problem+json" in resp_admin.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: vincular_alocacao retorna 201 em vínculo já existente em vez de 409 (H10.CA3)."
    ),
)
def test_h10_ca3_vincular_trabalhador_duplicado_no_mesmo_contrato_retorna_409(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """H10.CA3 (T-J2-05): Vincular o mesmo trabalhador duas vezes no mesmo contrato -> 409."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Alocacao Duplicada", 106)
    ctr_id = _criar_contrato_alfa(client, headers_admin_alfa, "CTR-ALOC-DUP-01")

    resp_1 = client.post(
        f"/contratos/{ctr_id}/alocacoes",
        json={"trabalhadorId": trb_id},
        headers=headers_admin_alfa,
    )
    assert resp_1.status_code == 201

    resp_2 = client.post(
        f"/contratos/{ctr_id}/alocacoes",
        json={"trabalhadorId": trb_id},
        headers=headers_admin_alfa,
    )
    assert resp_2.status_code == 409


def test_h11_ca1_ca2_reuso_documento_aprovado_em_segundo_contrato_semaforo_verde(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H11.CA1/CA2 (T-J2-06): Documento aprovado vale em novo contrato do mesmo tenant."""
    hoje = datetime.datetime.now(UTC).date()
    valido_90d = (hoje + datetime.timedelta(days=90)).isoformat()

    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Reuso Contratos", 107)

    # Envia e aprova ASO válido (> 30 dias)
    resp_up = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 aso reuso"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": valido_90d},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    resp_aprov = client.post(f"/documentos/{doc_id}/aprovacao", headers=headers_analista_alfa)
    assert resp_aprov.status_code == 200

    # Contrato A exigindo tip_aso -> VERDE
    ctr_a = _criar_contrato_alfa(client, headers_admin_alfa, "CTR-REUSO-A")
    assert (
        client.put(
            f"/contratos/{ctr_a}/exigencias",
            json={"tipoDocumentoIds": ["tip_aso"]},
            headers=headers_admin_alfa,
        ).status_code
        == 200
    )
    resp_aloc_a = client.post(
        f"/contratos/{ctr_a}/alocacoes",
        json={"trabalhadorId": trb_id},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_aloc_a.status_code == 201
    assert resp_aloc_a.json()["semaforo"] == "VERDE"
    assert resp_aloc_a.json()["pendencias"] == []

    # Contrato B exigindo apenas tip_aso -> sem novo upload, já nasce VERDE
    ctr_b = _criar_contrato_alfa(client, headers_admin_alfa, "CTR-REUSO-B")
    assert (
        client.put(
            f"/contratos/{ctr_b}/exigencias",
            json={"tipoDocumentoIds": ["tip_aso"]},
            headers=headers_admin_alfa,
        ).status_code
        == 200
    )
    resp_aloc_b = client.post(
        f"/contratos/{ctr_b}/alocacoes",
        json={"trabalhadorId": trb_id},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_aloc_b.status_code == 201
    assert resp_aloc_b.json()["semaforo"] == "VERDE"
    assert resp_aloc_b.json()["pendencias"] == []

    # Detalhe do trabalhador confirma ambos contratos em VERDE
    resp_det = client.get(f"/trabalhadores/{trb_id}", headers=headers_terceirizada_1_alfa)
    assert resp_det.status_code == 200
    alocs = {a["contratoId"]: a["semaforo"] for a in resp_det.json()["alocacoes"]}
    assert alocs[ctr_a] == "VERDE"
    assert alocs[ctr_b] == "VERDE"


def test_h11_ca3_reuso_nunca_atravessa_tenants(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
    headers_admin_beta: dict[str, str],
) -> None:
    """H11.CA3 (T-J2-07): Documento aprovado em Alfa não atende exigência do mesmo CPF em Beta."""
    hoje = datetime.datetime.now(UTC).date()
    valido_90d = (hoje + datetime.timedelta(days=90)).isoformat()
    cpf_compartilhado = _gerar_cpf_valido(108)

    # Cadastra trabalhador em Alfa com ASO aprovado
    resp_trb_a = client.post(
        "/trabalhadores",
        json={"nome": "Trab Alfa Reuso", "cpf": cpf_compartilhado, "terceirizadaId": TER_ALFA_1},
        headers=headers_admin_alfa,
    )
    assert resp_trb_a.status_code == 201
    trb_a_id = resp_trb_a.json()["id"]

    resp_up = client.post(
        f"/trabalhadores/{trb_a_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 alfa"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": valido_90d},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    assert (
        client.post(
            f"/documentos/{resp_up.json()['id']}/aprovacao",
            headers=headers_analista_alfa,
        ).status_code
        == 200
    )

    # Cadastra trabalhador com mesmo CPF em Beta (sem documentos em Beta)
    resp_trb_b = client.post(
        "/trabalhadores",
        json={"nome": "Trab Beta Isolado", "cpf": cpf_compartilhado, "terceirizadaId": TER_BETA_1},
        headers=headers_admin_beta,
    )
    assert resp_trb_b.status_code == 201
    trb_b_id = resp_trb_b.json()["id"]

    resp_ctr_b = client.post(
        "/contratos",
        json={
            "identificador": "CTR-BETA-ISOLADO-H11",
            "terceirizadaId": TER_BETA_1,
            "dataInicio": "2026-01-01",
            "dataFim": "2027-12-31",
        },
        headers=headers_admin_beta,
    )
    assert resp_ctr_b.status_code == 201
    ctr_b_id = resp_ctr_b.json()["id"]

    assert (
        client.put(
            f"/contratos/{ctr_b_id}/exigencias",
            json={"tipoDocumentoIds": ["tip_aso"]},
            headers=headers_admin_beta,
        ).status_code
        == 200
    )

    resp_aloc_b = client.post(
        f"/contratos/{ctr_b_id}/alocacoes",
        json={"trabalhadorId": trb_b_id},
        headers=headers_admin_beta,
    )
    assert resp_aloc_b.status_code == 201
    aloc_b = resp_aloc_b.json()
    assert aloc_b["semaforo"] == "VERMELHO"
    assert any(p["motivo"] == "AUSENTE" for p in aloc_b["pendencias"])


# =============================================================================
# Jornada J3 — Fila, Rejeição, Aprovação e Reenvio (H03, H12, H13, H14)
# =============================================================================


def _criar_doc_pendente_alfa(
    client: TestClient,
    trb_id: str,
    headers_terceirizada_1_alfa: dict[str, str],
) -> str:
    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("doc.pdf", io.BytesIO(b"%PDF-1.4 pendente"), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def test_h03_ca1_ca2_ca3_t_rej_01_03_04_05_06_particionamento_motivo_rejeicao(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H03.CA1/CA2/CA3 (T-REJ-01, 03, 04, 05, 06): Bordas de motivo de rejeição (10..500)."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Rejeicao BVA", 109)
    doc_id = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)

    # 1. Ausente (T-REJ-01), vazio, 9 chars (T-REJ-03) e 501 chars (H03.CA3 / T-REJ-06)
    for payload_invalido in (
        {},
        {"motivo": ""},
        {"motivo": "123456789"},
        {"motivo": "a" * 501},
    ):
        resp_err = client.post(
            f"/documentos/{doc_id}/rejeicao",
            json=payload_invalido,
            headers=headers_analista_alfa,
        )
        assert resp_err.status_code == 422
        assert "application/problem+json" in resp_err.headers.get("content-type", "")

    # Documento continua PENDENTE após tentativas inválidas (H03.CA2)
    resp_det = client.get(f"/documentos/{doc_id}", headers=headers_analista_alfa)
    assert resp_det.status_code == 200
    assert resp_det.json()["statusRevisao"] == "PENDENTE"

    # 2. Borda inferior: exatamente 10 caracteres (T-REJ-04) -> 200
    resp_10 = client.post(
        f"/documentos/{doc_id}/rejeicao",
        json={"motivo": "1234567890"},
        headers=headers_analista_alfa,
    )
    assert resp_10.status_code == 200
    assert resp_10.json()["statusRevisao"] == "REJEITADO"

    # 3. Borda superior: exatamente 500 caracteres (T-REJ-05) -> 200
    doc_id_500 = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)
    motivo_500 = "x" * 500
    resp_500 = client.post(
        f"/documentos/{doc_id_500}/rejeicao",
        json={"motivo": motivo_500},
        headers=headers_analista_alfa,
    )
    assert resp_500.status_code == 200
    assert resp_500.json()["statusRevisao"] == "REJEITADO"
    assert resp_500.json()["motivoRejeicao"] == motivo_500


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: RejeitarDocumentoPayload não remove espaços das pontas (strip) "
        "antes de validar min_length=10 (H03.CA2 / T-REJ-02)."
    ),
)
def test_h03_ca2_t_rej_02_motivo_apenas_espacos_rejeitado(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H03.CA2 (T-REJ-02): Motivo composto apenas por 10 espaços deve retornar 400/422."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Rej Espacos", 110)
    doc_id = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)

    resp = client.post(
        f"/documentos/{doc_id}/rejeicao",
        json={"motivo": "          "},
        headers=headers_analista_alfa,
    )
    assert resp.status_code in (400, 422)


def test_h03_ca4_rejeitar_documento_nao_pendente_retorna_409(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H03.CA4 (T-J3-02): Rejeitar documento APROVADO ou já REJEITADO retorna 409."""
    # 1. Documento já APROVADO
    resp_aprov = client.post(
        f"/documentos/{DOC_ALFA_1_ASO}/rejeicao",
        json={"motivo": "Tentativa de rejeitar documento aprovado"},
        headers=headers_analista_alfa,
    )
    assert resp_aprov.status_code == 409
    assert "application/problem+json" in resp_aprov.headers.get("content-type", "")

    # 2. Documento já REJEITADO
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Rej Conflito", 111)
    doc_id = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)
    assert (
        client.post(
            f"/documentos/{doc_id}/rejeicao",
            json={"motivo": "Primeira rejeição válida do documento"},
            headers=headers_analista_alfa,
        ).status_code
        == 200
    )

    resp_dup = client.post(
        f"/documentos/{doc_id}/rejeicao",
        json={"motivo": "Segunda rejeição que deve gerar conflito"},
        headers=headers_analista_alfa,
    )
    assert resp_dup.status_code == 409
    assert "application/problem+json" in resp_dup.headers.get("content-type", "")


def test_h12_ca1_fila_documentos_pendentes_filtro_e_limite_maximo_50(
    client: TestClient,
    headers_analista_alfa: dict[str, str],
) -> None:
    """H12.CA1 (T-J3-03): Fila filtrada por PENDENTE aceita limite=50 e recusa limite=51."""
    resp_ok = client.get(
        "/documentos?statusRevisao=PENDENTE&limite=50",
        headers=headers_analista_alfa,
    )
    assert resp_ok.status_code == 200
    dados = resp_ok.json()
    assert len(dados["itens"]) <= 50
    for item in dados["itens"]:
        assert item["statusRevisao"] == "PENDENTE"

    resp_51 = client.get(
        "/documentos?statusRevisao=PENDENTE&limite=51",
        headers=headers_analista_alfa,
    )
    assert resp_51.status_code == 422
    assert "application/problem+json" in resp_51.headers.get("content-type", "")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "BUG PR #32: aprovar_documento ainda não valida validoAte anterior a dataEmissao (H13.CA3)."
    ),
)
def test_h13_ca3_aprovar_documento_com_valido_ate_anterior_data_emissao_rejeitado(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H13.CA3 (T-J3-08): Aprovar documento com validoAte < dataEmissao deve retornar 400."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Aprovacao Datas", 112)
    resp_up = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("aso.pdf", io.BytesIO(b"%PDF-1.4 datas"), "application/pdf")},
        data={
            "tipoDocumentoId": "tip_aso",
            "dataEmissao": "2026-06-10",
            "validoAte": "2026-06-01",
        },
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_up.status_code == 201
    doc_id = resp_up.json()["id"]

    resp_aprov = client.post(f"/documentos/{doc_id}/aprovacao", headers=headers_analista_alfa)
    assert resp_aprov.status_code in (400, 422)


def test_h14_ca1_ca2_terceirizada_visualiza_motivo_rejeicao_e_reenvia_mantendo_historico(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    headers_analista_alfa: dict[str, str],
) -> None:
    """H14.CA1/CA2 (T-J3-09/10): Terceirizada vê motivo e reenvia mantendo histórico rejeitado."""
    trb_id = _criar_trabalhador_alfa(client, headers_admin_alfa, "Trab Reenvio Historico", 113)
    doc_1_id = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)

    motivo_exato = "Carimbo e CRM do médico examinador estão cortados na margem."
    resp_rej = client.post(
        f"/documentos/{doc_1_id}/rejeicao",
        json={"motivo": motivo_exato},
        headers=headers_analista_alfa,
    )
    assert resp_rej.status_code == 200

    # H14.CA1: terceirizada abre detalhe do documento e do trabalhador vendo o motivo exato
    resp_doc_terc = client.get(f"/documentos/{doc_1_id}", headers=headers_terceirizada_1_alfa)
    assert resp_doc_terc.status_code == 200
    assert resp_doc_terc.json()["motivoRejeicao"] == motivo_exato

    resp_trb_antes = client.get(f"/trabalhadores/{trb_id}", headers=headers_terceirizada_1_alfa)
    assert resp_trb_antes.status_code == 200
    docs_antes = resp_trb_antes.json()["documentos"]
    assert any(
        d["id"] == doc_1_id
        and d["statusRevisao"] == "REJEITADO"
        and d["motivoRejeicao"] == motivo_exato
        for d in docs_antes
    )

    # H14.CA2: terceirizada reenvia novo arquivo do mesmo tipo (tip_aso)
    doc_2_id = _criar_doc_pendente_alfa(client, trb_id, headers_terceirizada_1_alfa)
    assert doc_2_id != doc_1_id

    resp_trb_depois = client.get(f"/trabalhadores/{trb_id}", headers=headers_terceirizada_1_alfa)
    assert resp_trb_depois.status_code == 200
    docs_por_id = {d["id"]: d for d in resp_trb_depois.json()["documentos"]}
    assert docs_por_id[doc_1_id]["statusRevisao"] == "REJEITADO"
    assert docs_por_id[doc_1_id]["motivoRejeicao"] == motivo_exato
    assert docs_por_id[doc_2_id]["statusRevisao"] == "PENDENTE"
