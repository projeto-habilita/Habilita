"""Configuração da aplicação, lida de variáveis de ambiente.

Os valores padrão servem para o ambiente local. Se algum valor for inválido
(por exemplo PORT=abc), a aplicação falha ao iniciar, em vez de rodar errada.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    # Ambiente em que a API roda (APP_ENV ou ENV).
    app_env: Literal["local", "ci", "prod"] = Field(
        default="local",
        validation_alias=AliasChoices("APP_ENV", "ENV"),
    )
    # Nível de detalhe dos logs (LOG_LEVEL). Logs nunca devem ter dados pessoais.
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    # Porta em que a API escuta (PORT). O Render e a execução local definem PORT.
    port: int = Field(default=8000, ge=1, le=65535)
    # ID do projeto Firebase (FIREBASE_PROJECT_ID). `demo-` roda só nos emuladores.
    firebase_project_id: str = Field(default="demo-habilita", min_length=1, max_length=64)
    # Chave HMAC para anonimização/hashing de CPF (CPF_HMAC_KEY).
    cpf_hmac_key: str = Field(
        default="demo-cpf-hmac-key-for-emulator-only",
        min_length=16,
    )

    # Origens permitidas para CORS (CORS_ORIGINS).
    # NoDecode impede que o pydantic-settings tente fazer json.loads antes do validador.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        origens: list[str] = []
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json

                origens = [str(item).strip() for item in json.loads(v)]
            else:
                origens = [origem.strip() for origem in v.split(",") if origem.strip()]
        elif isinstance(v, (list, tuple, set)):
            origens = [str(item).strip() for item in v if str(item).strip()]

        if "*" in origens or any(origem == "*" for origem in origens):
            raise ValueError("A origem '*' não é permitida por segurança (RFC 6454 / ADR 0010).")

        return origens

    @model_validator(mode="after")
    def validar_seguranca(self) -> Settings:
        """Garante requisitos estritos de segurança por ambiente (BE-01, BE-03, ADR 0010)."""
        if self.app_env != "local" and (
            not self.cpf_hmac_key or self.cpf_hmac_key == "demo-cpf-hmac-key-for-emulator-only"
        ):
            raise ValueError(
                "CPF_HMAC_KEY deve ser configurada obrigatoriamente fora do ambiente local."
            )

        if self.app_env == "prod":
            if not self.cors_origins:
                raise ValueError("Em produção (APP_ENV=prod), CORS_ORIGINS não pode ser vazia.")
            for origem in self.cors_origins:
                if not origem.startswith("https://"):
                    raise ValueError(
                        f"Em produção (APP_ENV=prod), todas as origens devem usar HTTPS: {origem!r}"
                    )
        return self


def get_settings() -> Settings:
    return Settings()
