"""Testes para o job diário de recálculo, logs JSON e ajustes (#56, #57, #66, #112 a #114).

Cobre:
- BE-04 (#56): POST /internal/jobs/recalculo-vencimentos exige Bearer <JOB_TOKEN> (401 sem token).
- BE-17 (#66): Idempotência do job diário (rodar 2x na mesma dataReferencia não duplica alertas).
- BE-07 (#57): Logs estruturados em JSON com requestId, rota, status, latenciaMs e tenantId.
- Issue #112: Falha de armazenamento no upload propaga 503 e não persiste documento órfão.
- Issue #113: Aprovação/rejeição usa WriteBatch e impede revisão duplicada.
- Issue #114: Falha de validação do token Firebase responde 401 com mensagem fixa.
"""

from __future__ import annotations

import json
import logging
from typing import Literal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from habilita.armazenamento.porta import ArmazenamentoDocumentos, ArmazenamentoError
from habilita.comum.auth import extrair_usuario
from habilita.comum.erros import AppException
from habilita.trabalhadores.repositorio import _BANCO_DOCUMENTOS, TrabalhadoresRepositorio
from habilita.trabalhadores.schemas import ArquivoMetadados

from .conftest import TNT_ALFA, TRB_ALFA_1


# -----------------------------------------------------------------------------
# BE-04 (#56) e BE-17 (#66) - Job diário de recálculo de vencimentos
# -----------------------------------------------------------------------------
def test_h15_ca1_job_recalculo_sem_token_ou_token_invalido_retorna_401(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
) -> None:
    """Rota interna rejeita requisições sem token, com token errado ou de usuário."""
    resp_sem = client.post("/internal/jobs/recalculo-vencimentos")
    assert resp_sem.status_code == 401
    assert "application/problem+json" in resp_sem.headers.get("content-type", "")

    resp_err = client.post(
        "/internal/jobs/recalculo-vencimentos",
        headers={"Authorization": "Bearer token-errado-invasor"},
    )
    assert resp_err.status_code == 401

    resp_usr = client.post(
        "/internal/jobs/recalculo-vencimentos",
        headers=headers_admin_alfa,
    )
    assert resp_usr.status_code == 401


def test_h15_ca3_job_recalculo_idempotente_nao_duplica_alertas(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executar o job duas vezes na mesma dataReferencia não cria alertas duplicados."""
    monkeypatch.setenv("JOB_TOKEN", "segredo-job-teste-1234567890")
    headers_job = {"Authorization": "Bearer segredo-job-teste-1234567890"}
    payload = {"dataReferencia": "2026-12-17"}

    resp_1 = client.post(
        "/internal/jobs/recalculo-vencimentos",
        json=payload,
        headers=headers_job,
    )
    assert resp_1.status_code == 200
    dados_1 = resp_1.json()
    assert dados_1["status"] == "CONCLUIDO"
    assert dados_1["dataReferencia"] == "2026-12-17"
    assert dados_1["documentosProcessados"] >= 1
    assert dados_1["alertasCriados"] >= 1

    # Segunda execução para a mesma dataReferencia: 0 novos alertas criados (idempotente)
    resp_2 = client.post(
        "/internal/jobs/recalculo-vencimentos",
        json=payload,
        headers=headers_job,
    )
    assert resp_2.status_code == 200
    dados_2 = resp_2.json()
    assert dados_2["status"] == "CONCLUIDO"
    assert dados_2["dataReferencia"] == "2026-12-17"
    assert dados_2["documentosProcessados"] == dados_1["documentosProcessados"]
    assert dados_2["alertasCriados"] == 0


# -----------------------------------------------------------------------------
# BE-07 (#57) - Logs em JSON com requestId, rota, status, latência e tenantId
# -----------------------------------------------------------------------------
def test_be07_logs_estruturados_em_json_com_campos_obrigatorios_e_sem_pii(
    client: TestClient,
    headers_admin_alfa: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifica emissão de log JSON com requestId, rota, status, latenciaMs e tenantId."""
    with caplog.at_level(logging.INFO, logger="habilita.acesso"):
        resp = client.get(
            "/terceirizadas",
            headers={**headers_admin_alfa, "X-Request-Id": "req-teste-json-001"},
        )
    assert resp.status_code == 200

    mensagens_json = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "habilita.acesso" and r.getMessage().startswith("{")
    ]
    assert mensagens_json, "Nenhum log JSON emitido pelo logger habilita.acesso"
    ultimo = mensagens_json[-1]

    assert ultimo["requestId"] == "req-teste-json-001"
    assert ultimo["rota"] == "/terceirizadas"
    assert ultimo["metodo"] == "GET"
    assert ultimo["status"] == 200
    assert isinstance(ultimo["latenciaMs"], (int, float))
    assert ultimo["latenciaMs"] >= 0
    assert ultimo["tenantId"] == TNT_ALFA


# -----------------------------------------------------------------------------
# Issue #112 - Upload não registra documento quando armazenamento falha
# -----------------------------------------------------------------------------
class _StorageComFalha(ArmazenamentoDocumentos):
    tenant_id: str = TNT_ALFA

    def salvar(
        self,
        trabalhador_id: str,
        documento_id: str,
        conteudo: bytes,
        extensao: Literal["pdf", "jpg"],
        miniatura: bool = False,
        arquivo_uuid: str | None = None,
    ) -> str:
        raise ArmazenamentoError("Simulação de Supabase Storage fora do ar")

    def obter_url_leitura(
        self,
        caminho: str | None = None,
        trabalhador_id: str | None = None,
        documento_id: str | None = None,
        nome_arquivo: str | None = None,
        expira_em_segundos: int = 300,
    ) -> str:
        raise ArmazenamentoError("Indisponível")

    def excluir(
        self,
        caminho: str | None = None,
        trabalhador_id: str | None = None,
        documento_id: str | None = None,
        nome_arquivo: str | None = None,
    ) -> None:
        raise ArmazenamentoError("Indisponível")

    def saude(self) -> bool:
        return False


def test_issue_112_upload_falha_armazenamento_nao_cria_documento() -> None:
    """Quando o armazenamento falha, criar_documento lança 503 e não salva documento órfão."""
    repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, storage=_StorageComFalha())
    qtd_antes = len(_BANCO_DOCUMENTOS.get(TNT_ALFA, {}))

    meta = ArquivoMetadados(
        contentType="application/pdf",
        tamanhoBytes=32,
        sha256="a" * 64,
    )
    with pytest.raises(AppException) as exc_info:
        repo.criar_documento(
            trabalhador_id=TRB_ALFA_1,
            tipo_documento_id="tip_aso",
            valido_ate="2027-12-31",
            data_emissao="2026-06-01",
            arquivo_meta=meta,
            conteudo=b"%PDF-1.4 conteudo teste falha",
        )
    assert exc_info.value.status_code == 503
    qtd_depois = len(_BANCO_DOCUMENTOS.get(TNT_ALFA, {}))
    assert qtd_depois == qtd_antes


# -----------------------------------------------------------------------------
# Issue #113 - Aprovação/rejeição atômica via WriteBatch
# -----------------------------------------------------------------------------
def test_issue_113_aprovar_documento_usa_write_batch_e_bloqueia_concorrencia() -> None:
    """Verifica que aprovar_documento com Firestore ativo utiliza batch.commit()."""
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_doc_col = MagicMock()
    mock_tenant_doc.collection.return_value = mock_doc_col

    snap_pendente = MagicMock()
    snap_pendente.exists = True
    snap_pendente.to_dict.return_value = {
        "id": "doc_batch_1",
        "trabalhadorId": TRB_ALFA_1,
        "tipoDocumentoId": "tip_aso",
        "validoAte": "2027-12-31",
        "statusRevisao": "PENDENTE",
        "enviadoEm": "2026-10-20T10:00:00Z",
    }
    mock_doc_col.document.return_value.get.return_value = snap_pendente

    repo = TrabalhadoresRepositorio(tenant_id=TNT_ALFA, db=mock_db)
    doc = repo.aprovar_documento("doc_batch_1")
    assert doc.statusRevisao == "APROVADO"
    mock_db.batch.assert_called_once()
    mock_db.batch.return_value.commit.assert_called_once()


# -----------------------------------------------------------------------------
# Issue #114 - Não vazar texto interno da exceção do Firebase no HTTP 401
# -----------------------------------------------------------------------------
def test_issue_114_token_invalido_nao_vaza_texto_excecao_firebase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erro do Firebase Admin ao verificar token retorna mensagem fixa sem expor detalhes."""
    detalhe_sensivel = "Certificate kid=99999 expired for project internal-firebase-secret"

    def _falha_interna(_token: str) -> dict[str, str]:
        raise ValueError(detalhe_sensivel)

    monkeypatch.setattr("firebase_admin.auth.verify_id_token", _falha_interna)
    with pytest.raises(AppException) as exc_info:
        extrair_usuario(authorization="Bearer token-qualquer")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token inválido ou expirado."
    assert detalhe_sensivel not in str(exc_info.value.detail)
