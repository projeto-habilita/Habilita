"""Verificação de integridade e prontidão da API.

- `/healthz`: verificação de vida (liveness), estritamente leve e sem dependências externas.
- `/readyz`: prontidão (readiness), consultando dependências essenciais (Cloud Firestore).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from habilita.comum.firestore import obter_firestore_client
from habilita.config import get_settings

router = APIRouter(tags=["Sistema"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


@router.get("/healthz", response_model=HealthResponse, operation_id="healthz")
def healthz() -> HealthResponse:
    """Verificação de vida da API."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadyResponse, operation_id="readyz")
def readyz() -> ReadyResponse:
    """Verificação de prontidão (dependências acessíveis)."""
    settings = get_settings()
    db = obter_firestore_client()

    if db is None:
        if settings.app_env != "local":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Serviço indisponível: conexão com Firestore não inicializada.",
            )
        # Em desenvolvimento local sem emuladores, aceita pronto em modo de memória local
        return ReadyResponse(status="ok")

    try:
        # Consulta real e rápida ao Firestore para atestar conectividade
        _ = list(db.collection("tenants").limit(1).stream())
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Serviço indisponível: falha ao consultar Firestore ({exc}).",
        ) from exc

    return ReadyResponse(status="ok")
