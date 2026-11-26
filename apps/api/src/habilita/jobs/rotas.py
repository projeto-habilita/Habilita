"""Rota interna de agendamento: recálculo diário de vencimentos e expurgo (H15 / ADR 0007).

Conforme o contrato OpenAPI (`POST /internal/jobs/recalculo-vencimentos`), ADR 0006 §3.4 e ADR 0007:
- Autenticação exclusiva com `Authorization: Bearer <JOB_TOKEN>` comparada em tempo constante.
- Sem token ou com token inválido (inclusive um JWT do Firebase Auth) -> HTTP 401 Problem Details.
- Recalcula `situacaoValidade` (`VALIDO`, `A_VENCER`, `VENCIDO`) dos documentos aprovados.
- Recalcula semáforos das alocações de cada tenant.
- Gera alertas de vencimento com ID determinístico (`alr_{docId}_{marco}_{dataRef}`) para
  garantir idempotência estrita (rodar múltiplas vezes para a mesma data não duplica alertas).
- Executa expurgo idempotente dos arquivos cujo `expurgoPrevistoEm` já venceu.
"""

from __future__ import annotations

import datetime
import hmac
import logging
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Header
from google.cloud.firestore import Client as FirestoreClient
from pydantic import BaseModel, ConfigDict, Field

from habilita.armazenamento import ArmazenamentoError, SupabaseArmazenamento
from habilita.comum.erros import nao_autenticado, requisicao_invalida
from habilita.comum.firestore import obter_firestore_client
from habilita.config import CHAVE_DEMO_SUPABASE, get_settings
from habilita.trabalhadores.repositorio import (
    _BANCO_DOCUMENTOS,
    _BANCO_TRABALHADORES,
    TrabalhadoresRepositorio,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Interno"])

TZ_SAO_PAULO = datetime.timezone(datetime.timedelta(hours=-3), name="America/Sao_Paulo")
UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017

_BANCO_ALERTAS: dict[str, dict[str, dict[str, Any]]] = {}


class RecalculoVencimentosPayload(BaseModel):
    """Corpo opcional para execução do job diário."""

    model_config = ConfigDict(extra="forbid")

    dataReferencia: str | None = Field(
        default=None,
        description="Data de referência YYYY-MM-DD (padrão = hoje em America/Sao_Paulo).",
    )


class ExecucaoJobResponse(BaseModel):
    """Resultado consolidado da execução do job diário (schema ExecucaoJob do OpenAPI)."""

    model_config = ConfigDict(extra="forbid")

    dataReferencia: str
    status: Literal["CONCLUIDO", "FALHOU"]
    documentosProcessados: int = Field(ge=0)
    alertasCriados: int = Field(ge=0)
    duracaoMs: int = Field(ge=0)


def validar_job_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Valida o cabeçalho Authorization: Bearer <JOB_TOKEN> em tempo constante."""
    if not authorization or not authorization.startswith("Bearer "):
        raise nao_autenticado("Token de job ausente ou formato inválido.")

    token_recebido = authorization.removeprefix("Bearer ").strip()
    token_esperado = get_settings().job_token.get_secret_value().strip()

    if not token_recebido or not token_esperado:
        raise nao_autenticado("Token de job inválido.")

    if not hmac.compare_digest(
        token_recebido.encode("utf-8"),
        token_esperado.encode("utf-8"),
    ):
        logger.warning(
            "Tentativa não autorizada de acionar job interno",
            extra={"evento": "job_token_invalido"},
        )
        raise nao_autenticado("Token de job inválido.")


def _calcular_situacao_e_marco(
    valido_ate_str: str,
    data_ref: datetime.date,
) -> tuple[Literal["VALIDO", "A_VENCER", "VENCIDO"], str | None]:
    """Calcula a situação de validade (RN-04) e o marco de alerta caso aplicável."""
    try:
        dt_valido = datetime.date.fromisoformat(valido_ate_str)
        dias = (dt_valido - data_ref).days
    except ValueError:
        return "VENCIDO", "VENCIDO"

    if dias < 0:
        return "VENCIDO", "VENCIDO"
    if dias <= 30:
        return "A_VENCER", "A_VENCER_30D"
    return "VALIDO", None


def _registrar_alerta_idempotente(
    db: FirestoreClient | None,
    tenant_id: str,
    alerta_id: str,
    alerta_payload: dict[str, Any],
) -> bool:
    """Persiste alerta apenas se ainda não existir para aquele ID determinístico.

    Retorna True se um novo alerta foi criado nesta execução, False se já existia.
    """
    if db is not None:
        alerta_ref = (
            db.collection("tenants").document(tenant_id).collection("alertas").document(alerta_id)
        )
        if alerta_ref.get().exists:
            return False
        alerta_ref.set(alerta_payload)
        if get_settings().app_env == "local":
            _BANCO_ALERTAS.setdefault(tenant_id, {})[alerta_id] = alerta_payload
        return True

    alertas_tenant = _BANCO_ALERTAS.setdefault(tenant_id, {})
    if alerta_id in alertas_tenant:
        return False
    alertas_tenant[alerta_id] = alerta_payload
    return True


def _expurgar_se_vencido(
    tenant_id: str,
    doc_dados: dict[str, Any],
    data_ref: datetime.date,
) -> bool:
    """Expurga arquivo no Storage de forma idempotente se expurgoPrevistoEm <= dataReferencia."""
    expurgo_str = doc_dados.get("expurgoPrevistoEm")
    if not isinstance(expurgo_str, str) or not expurgo_str:
        return False

    try:
        dt_expurgo = datetime.date.fromisoformat(expurgo_str[:10])
    except ValueError:
        return False

    if dt_expurgo > data_ref:
        return False

    arquivo = doc_dados.get("arquivo")
    caminho = arquivo.get("caminho") if isinstance(arquivo, dict) else None
    if not isinstance(caminho, str) or not caminho:
        return False

    cfg = get_settings()
    chave = cfg.supabase_service_key.get_secret_value().strip() or CHAVE_DEMO_SUPABASE
    storage = SupabaseArmazenamento(
        tenant_id=tenant_id,
        base_url=cfg.storage_url,
        service_key=chave,
        bucket=cfg.storage_bucket,
        public_url=cfg.storage_public_url,
        timeout_segundos=1.0 if cfg.app_env == "local" else 5.0,
    )
    try:
        storage.excluir(caminho=caminho)
        return True
    except ArmazenamentoError:
        return False


def executar_recalculo_para_data(data_ref: datetime.date) -> tuple[int, int]:
    """Executa recálculo idempotente de documentos, alocações e alertas em todos os tenants."""
    db = obter_firestore_client()
    data_ref_iso = data_ref.isoformat()
    agora_iso = datetime.datetime.now(UTC).isoformat()

    docs_processados = 0
    alertas_criados = 0

    tenants_ids: set[str] = set()
    if db is not None:
        for t_doc in db.collection("tenants").stream():
            tenants_ids.add(t_doc.id)
    if not tenants_ids and get_settings().app_env == "local":
        tenants_ids.update(_BANCO_DOCUMENTOS.keys())
        tenants_ids.update(_BANCO_TRABALHADORES.keys())

    for tenant_id in sorted(tenants_ids):
        docs_tenant: dict[str, dict[str, Any]] = {}
        if db is not None:
            t_ref = db.collection("tenants").document(tenant_id)
            for d in t_ref.collection("documentos").stream():
                dados = d.to_dict()
                if dados and "id" in dados:
                    docs_tenant[str(dados["id"])] = dados
            for trb_snap in t_ref.collection("trabalhadores").stream():
                for sub_d in trb_snap.reference.collection("documentos").stream():
                    dados = sub_d.to_dict()
                    if dados and "id" in dados:
                        docs_tenant[str(dados["id"])] = dados

        if not docs_tenant and get_settings().app_env == "local":
            for doc_id, dados in _BANCO_DOCUMENTOS.get(tenant_id, {}).items():
                docs_tenant[doc_id] = dados

        for doc_id, doc_dados in docs_tenant.items():
            _expurgar_se_vencido(tenant_id, doc_dados, data_ref)
            if doc_dados.get("statusRevisao") != "APROVADO":
                continue

            docs_processados += 1
            valido_ate_str = str(doc_dados.get("validoAte", ""))
            nova_situacao, marco = _calcular_situacao_e_marco(valido_ate_str, data_ref)

            if doc_dados.get("situacaoValidade") != nova_situacao:
                doc_dados["situacaoValidade"] = nova_situacao
                if db is not None:
                    t_ref = db.collection("tenants").document(tenant_id)
                    d_ref = t_ref.collection("documentos").document(doc_id)
                    if d_ref.get().exists:
                        d_ref.update({"situacaoValidade": nova_situacao})
                    trb_id = str(doc_dados.get("trabalhadorId", ""))
                    if trb_id:
                        sub_ref = (
                            t_ref.collection("trabalhadores")
                            .document(trb_id)
                            .collection("documentos")
                            .document(doc_id)
                        )
                        if sub_ref.get().exists:
                            sub_ref.update({"situacaoValidade": nova_situacao})
                if get_settings().app_env == "local":
                    mem_doc = _BANCO_DOCUMENTOS.get(tenant_id, {}).get(doc_id)
                    if mem_doc is not None:
                        mem_doc["situacaoValidade"] = nova_situacao

            if marco is not None:
                alerta_id = f"alr_{doc_id}_{marco}_{data_ref_iso}"
                alerta_payload = {
                    "id": alerta_id,
                    "documentoId": doc_id,
                    "trabalhadorId": doc_dados.get("trabalhadorId"),
                    "tipoDocumentoId": doc_dados.get("tipoDocumentoId"),
                    "marco": marco,
                    "situacaoValidade": nova_situacao,
                    "validoAte": valido_ate_str,
                    "dataReferencia": data_ref_iso,
                    "criadoEm": agora_iso,
                }
                if _registrar_alerta_idempotente(db, tenant_id, alerta_id, alerta_payload):
                    alertas_criados += 1

        # Recalcula semáforos das alocações do tenant
        repo_trb = TrabalhadoresRepositorio(tenant_id=tenant_id, db=db)
        trabalhadores_lista, _ = repo_trb.listar_trabalhadores(limite=50)
        for trb_resumo in trabalhadores_lista:
            trb_det = repo_trb.obter_trabalhador(trb_resumo.id)
            if not trb_det:
                continue
            for aloc in trb_det.alocacoes:
                nova_aloc = repo_trb.calcular_semaforo(
                    aloc.contratoId,
                    trb_resumo.id,
                    data_referencia=data_ref,
                )
                if db is not None:
                    (
                        db.collection("tenants")
                        .document(tenant_id)
                        .collection("contratos")
                        .document(aloc.contratoId)
                        .collection("alocacoes")
                        .document(trb_resumo.id)
                        .set(nova_aloc.model_dump())
                    )

    return docs_processados, alertas_criados


@router.post(
    "/internal/jobs/recalculo-vencimentos",
    response_model=ExecucaoJobResponse,
    operation_id="executarRecalculoVencimentos",
    summary="Recalcular situações, semáforos e alertas (idempotente)",
)
def executar_recalculo_vencimentos(
    authorization: Annotated[str | None, Header()] = None,
    payload: Annotated[RecalculoVencimentosPayload | None, Body()] = None,
) -> ExecucaoJobResponse:
    """Executa o job diário idempotente de recálculo de vencimentos e expurgo (H15)."""
    inicio = time.perf_counter()
    validar_job_token(authorization)

    if payload and payload.dataReferencia:
        try:
            data_ref = datetime.date.fromisoformat(payload.dataReferencia)
        except ValueError:
            raise requisicao_invalida(
                "Campo 'dataReferencia' deve estar no formato YYYY-MM-DD."
            ) from None
    else:
        data_ref = datetime.datetime.now(TZ_SAO_PAULO).date()

    docs_proc, alertas_novos = executar_recalculo_para_data(data_ref)
    duracao_ms = int((time.perf_counter() - inicio) * 1000)

    logger.info(
        "Job diário de recálculo concluído",
        extra={
            "evento": "job_recalculo_concluido",
            "dataReferencia": data_ref.isoformat(),
            "documentosProcessados": docs_proc,
            "alertasCriados": alertas_novos,
            "duracaoMs": duracao_ms,
        },
    )

    return ExecucaoJobResponse(
        dataReferencia=data_ref.isoformat(),
        status="CONCLUIDO",
        documentosProcessados=docs_proc,
        alertasCriados=alertas_novos,
        duracaoMs=duracao_ms,
    )
