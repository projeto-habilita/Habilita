"""Suíte BVA (Análise de Valor Limite) de upload de documentos ([QA-13] #88).

Cobre rigorosamente os limites de tamanho, payload vazio, assinaturas mágicas
inválidas e conformidade RFC 9457 (application/problem+json) para H02.CA3-CA5/CA7.
"""

from __future__ import annotations

import hashlib
import io
import itertools
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import TER_ALFA_1

TEXTO_PDF_FICTICIO = "DOCUMENTO FICTÍCIO — TESTE AUTOMATIZADO HABILITA".encode()
CABECALHO_PDF_FICTICIO = b"%PDF-1.4\n" + TEXTO_PDF_FICTICIO + b"\n"
CABECALHO_JPEG_FICTICIO = b"\xff\xd8\xff\xe0" + TEXTO_PDF_FICTICIO


_CONTADOR_CPF = itertools.count(1)


def _gerar_cpf_ficticio(semente: int) -> str:
    """Gera CPF matemático fictício válido para testes automatizados (sem PII real)."""
    base = f"{910000000 + semente:09d}"
    d1 = sum(int(base[i]) * (10 - i) for i in range(9)) % 11
    d1 = 0 if d1 < 2 else 11 - d1
    b10 = base + str(d1)
    d2 = sum(int(b10[i]) * (11 - i) for i in range(10)) % 11
    d2 = 0 if d2 < 2 else 11 - d2
    return f"{base}{d1}{d2}"


def _criar_trabalhador_ficticio(
    client: TestClient,
    headers_admin: dict[str, str],
    _semente_ignorado: int = 0,
) -> str:
    semente = next(_CONTADOR_CPF)
    resp = client.post(
        "/trabalhadores",
        json={
            "nome": f"Trabalhador Limite {semente}",
            "cpf": _gerar_cpf_ficticio(semente),
            "terceirizadaId": TER_ALFA_1,
        },
        headers=headers_admin,
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _construir_pdf_exato(tamanho_bytes: int) -> bytes:
    assert tamanho_bytes >= len(CABECALHO_PDF_FICTICIO)
    preenchimento = b"0" * (tamanho_bytes - len(CABECALHO_PDF_FICTICIO))
    return CABECALHO_PDF_FICTICIO + preenchimento


def _construir_jpeg_exato(tamanho_bytes: int) -> bytes:
    assert tamanho_bytes >= len(CABECALHO_JPEG_FICTICIO)
    preenchimento = b"\x00" * (tamanho_bytes - len(CABECALHO_JPEG_FICTICIO))
    return CABECALHO_JPEG_FICTICIO + preenchimento


def _validar_rfc9457(resp: httpx.Response, status_esperado: int) -> dict[str, Any]:
    """Valida que a resposta segue estritamente RFC 9457 Problem Details."""
    assert resp.status_code == status_esperado
    content_type = resp.headers.get("content-type", "")
    assert "application/problem+json" in content_type
    assert "x-request-id" in resp.headers

    corpo: dict[str, Any] = resp.json()
    assert corpo.get("status") == status_esperado
    assert isinstance(corpo.get("type"), str) and corpo["type"]
    assert isinstance(corpo.get("title"), str) and corpo["title"]
    assert isinstance(corpo.get("detail"), str) and corpo["detail"]
    assert isinstance(corpo.get("instance"), str) and corpo["instance"]
    return corpo


@pytest.mark.parametrize(
    "tamanho_bytes",
    [
        pytest.param(4_900_000, id="pdf_4_9MB"),
        pytest.param(4_999_999, id="pdf_4_999_999B"),
        pytest.param(5_000_000, id="pdf_5_0MB_exato"),
    ],
)
def test_qa13_upload_pdf_dentro_do_limite_retorna_201(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    tamanho_bytes: int,
) -> None:
    """BVA inferior/exato (4,9 MB, 4.999.999 B e 5.000.000 B): PDF aceito com HTTP 201."""
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, tamanho_bytes % 10000 + 1)
    conteudo = _construir_pdf_exato(tamanho_bytes)
    assert len(conteudo) == tamanho_bytes

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("exame.pdf", io.BytesIO(conteudo), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code == 201
    doc = resp.json()
    assert doc["statusRevisao"] == "PENDENTE"
    assert doc["arquivo"]["contentType"] == "application/pdf"
    assert doc["arquivo"]["tamanhoBytes"] == tamanho_bytes
    assert doc["arquivo"]["sha256"] == hashlib.sha256(conteudo).hexdigest()


@pytest.mark.parametrize(
    "tamanho_bytes",
    [
        pytest.param(4_900_000, id="jpg_4_9MB"),
        pytest.param(4_999_999, id="jpg_4_999_999B"),
        pytest.param(5_000_000, id="jpg_5_0MB_exato"),
    ],
)
def test_qa13_upload_jpeg_dentro_do_limite_retorna_201(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    tamanho_bytes: int,
) -> None:
    """BVA inferior/exato: JPEG de 4,9 MB, 4.999.999 B e 5.000.000 B aceito com HTTP 201."""
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, tamanho_bytes % 10000 + 20)
    conteudo = _construir_jpeg_exato(tamanho_bytes)
    assert len(conteudo) == tamanho_bytes

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("exame.jpg", io.BytesIO(conteudo), "image/jpeg")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    assert resp.status_code == 201
    doc = resp.json()
    assert doc["statusRevisao"] == "PENDENTE"
    assert doc["arquivo"]["contentType"] == "image/jpeg"
    assert doc["arquivo"]["tamanhoBytes"] == tamanho_bytes


@pytest.mark.parametrize(
    ("formato", "tamanho_bytes"),
    [
        pytest.param("pdf", 5_000_001, id="pdf_5_000_001B"),
        pytest.param("pdf", 5_100_000, id="pdf_5_1MB"),
        pytest.param("jpg", 5_000_001, id="jpg_5_000_001B"),
        pytest.param("jpg", 5_100_000, id="jpg_5_1MB"),
    ],
)
def test_qa13_upload_acima_do_limite_retorna_413_rfc9457(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    formato: str,
    tamanho_bytes: int,
) -> None:
    """BVA superior (5.000.001 B e 5,1 MB): excedente rejeitado com 413 Problem Details."""
    semente = (tamanho_bytes % 10000) + (100 if formato == "pdf" else 200)
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, semente)
    if formato == "pdf":
        conteudo = _construir_pdf_exato(tamanho_bytes)
        mime = "application/pdf"
    else:
        conteudo = _construir_jpeg_exato(tamanho_bytes)
        mime = "image/jpeg"

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": (f"grande.{formato}", io.BytesIO(conteudo), mime)},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    corpo = _validar_rfc9457(resp, 413)
    assert "5000000" in corpo["detail"]


@pytest.mark.parametrize(
    ("nome_arquivo", "content_type"),
    [
        ("vazio.pdf", "application/pdf"),
        ("vazio.jpg", "image/jpeg"),
    ],
)
def test_qa13_upload_payload_vazio_retorna_400_rfc9457(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    nome_arquivo: str,
    content_type: str,
) -> None:
    """Payload de 0 bytes é rejeitado com HTTP 400 Problem Details (RFC 9457)."""
    semente = 301 if content_type == "application/pdf" else 302
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, semente)

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": (nome_arquivo, io.BytesIO(b""), content_type)},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    _validar_rfc9457(resp, 400)


@pytest.mark.parametrize(
    ("nome_arquivo", "payload", "content_type_declarado"),
    [
        pytest.param(
            "executavel.pdf",
            b"MZ\x90\x00" + b"\x00" * 64,
            "application/pdf",
            id="executavel_mz_como_pdf",
        ),
        pytest.param(
            "texto_puro.pdf",
            TEXTO_PDF_FICTICIO,
            "application/pdf",
            id="texto_puro_como_pdf",
        ),
        pytest.param(
            "imagem.pdf",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
            "application/pdf",
            id="png_como_pdf",
        ),
        pytest.param(
            "binario.pdf",
            b"\x7fELF" + b"\x00" * 32,
            "application/pdf",
            id="elf_como_pdf",
        ),
        pytest.param(
            "compactado.pdf",
            b"PK\x03\x04" + b"\x00" * 32,
            "application/pdf",
            id="zip_como_pdf",
        ),
        pytest.param(
            "animacao.jpg",
            b"GIF89a" + b"\x00" * 32,
            "image/jpeg",
            id="gif_como_jpeg",
        ),
        pytest.param(
            "pdf_truncado.pdf",
            b"%PD",
            "application/pdf",
            id="assinatura_pdf_incompleta",
        ),
        pytest.param(
            "jpeg_truncado.jpg",
            b"\xff\xd8",
            "image/jpeg",
            id="assinatura_jpeg_incompleta",
        ),
    ],
)
def test_qa13_assinatura_magica_invalida_retorna_415_rfc9457(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
    nome_arquivo: str,
    payload: bytes,
    content_type_declarado: str,
) -> None:
    """Assinaturas mágicas inválidas disfarçadas de PDF/JPG retornam 415 RFC 9457."""
    semente = 400 + (sum(payload[:4]) % 500)
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, semente)

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": (nome_arquivo, io.BytesIO(payload), content_type_declarado)},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    _validar_rfc9457(resp, 415)


def test_qa13_excesso_de_tamanho_com_assinatura_invalida_retorna_413(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """Arquivo > 5.000.000 bytes com assinatura MZ é barrado por tamanho (413) antes do MIME."""
    trb_id = _criar_trabalhador_ficticio(client, headers_admin_alfa, 950)
    conteudo = b"MZ\x90\x00" + b"\x00" * 4_999_997
    assert len(conteudo) == 5_000_001

    resp = client.post(
        f"/trabalhadores/{trb_id}/documentos",
        files={"arquivo": ("grande_mz.pdf", io.BytesIO(conteudo), "application/pdf")},
        data={"tipoDocumentoId": "tip_aso", "validoAte": "2027-12-31"},
        headers=headers_terceirizada_1_alfa,
    )
    _validar_rfc9457(resp, 413)
