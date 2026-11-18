"""Autenticação e controle de acesso multi-tenant.

Conforme a ADR 0002 e o AGENTS.md:
- O tenantId vem SEMPRE da claim do token (nunca da requisição).
- Papel não permitido responde HTTP 403 (Problem Details).
- Ausência ou invalidade de token responde HTTP 401.
- Recurso de outro tenant (ou outra terceirizada) responde HTTP 404.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header

from habilita.comum.erros import acesso_proibido, nao_autenticado

logger = logging.getLogger(__name__)


class UsuarioAutenticado:
    """Dados de identidade e autorização do usuário extraídos das custom claims."""

    def __init__(
        self,
        uid: str,
        tenant_id: str,
        papel: str,
        terceirizada_id: str | None = None,
    ) -> None:
        self.uid = uid
        self.tenant_id = tenant_id
        self.papel = papel
        self.terceirizada_id = terceirizada_id

    def __repr__(self) -> str:
        return (
            f"UsuarioAutenticado(uid={self.uid}, tenant_id={self.tenant_id}, "
            f"papel={self.papel}, terceirizada_id={self.terceirizada_id})"
        )


def extrair_usuario(
    authorization: Annotated[str | None, Header()] = None,
) -> UsuarioAutenticado:
    """Extrai e valida a identidade do usuário autenticado a partir do token Bearer."""
    if not authorization or not authorization.startswith("Bearer "):
        raise nao_autenticado("Cabeçalho de autorização Bearer ausente ou inválido.")

    token = authorization.removeprefix("Bearer ").strip()

    # Autenticação real com Firebase Admin SDK. Só a verificação do token vira 401.
    try:
        from firebase_admin import auth  # type: ignore[import-untyped]

        decoded_claims: dict[str, Any] = auth.verify_id_token(token)
    except Exception as exc:
        raise nao_autenticado(f"Falha ao validar token Firebase: {exc}") from exc

    uid = str(decoded_claims.get("uid", decoded_claims.get("user_id", "")))
    tenant_id = decoded_claims.get("tenantId")
    papel = decoded_claims.get("papel")
    terceirizada_id = decoded_claims.get("terceirizadaId")

    # H07.CA4: token válido, mas sem claims de autorização -> 403 (não 401).
    # O log registra só o motivo, sem uid, e-mail ou qualquer dado pessoal.
    if not tenant_id or not papel:
        logger.warning(
            "Acesso negado: token sem claims obrigatórias",
            extra={"evento": "claims_ausentes", "tenantId": bool(tenant_id), "papel": bool(papel)},
        )
        raise acesso_proibido("Token não contém as custom claims necessárias (tenantId, papel).")

    return UsuarioAutenticado(
        uid=uid,
        tenant_id=str(tenant_id),
        papel=str(papel),
        terceirizada_id=str(terceirizada_id) if terceirizada_id else None,
    )


def exigir_papeis(*papeis_permitidos: str) -> Callable[[UsuarioAutenticado], UsuarioAutenticado]:
    """Dependency factory que garante que o usuário autenticado possui um dos papéis permitidos."""

    def _verificar_papel(
        usuario: Annotated[UsuarioAutenticado, Depends(extrair_usuario)],
    ) -> UsuarioAutenticado:
        if usuario.papel not in papeis_permitidos:
            raise acesso_proibido(
                f"Papel '{usuario.papel}' não tem permissão para esta rota. "
                f"Papéis permitidos: {', '.join(papeis_permitidos)}."
            )
        return usuario

    return _verificar_papel
