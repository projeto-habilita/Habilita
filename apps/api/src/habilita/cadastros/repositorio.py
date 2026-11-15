"""Repositório de dados para J1 com persistência no Firestore e isolamento estrito por tenant.

Conforme a ADR 0002 e o AGENTS.md:
- O repositório recebe o tenantId no construtor.
- Nenhuma operação pode acessar dados fora do tenant do construtor (tenants/{tenantId}/...).
- Grava diretamente no Firestore quando disponível (emuladores/produção).
- Mantém fallback para memória determinística em testes unitários offline.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from google.cloud.firestore import Client as FirestoreClient

from habilita.cadastros.schemas import (
    Contrato,
    NovaTerceirizada,
    NovoContrato,
    NovoTipoDocumento,
    Terceirizada,
    TipoDocumento,
)
from habilita.comum.firestore import obter_firestore_client

UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017

# Armazenamento determinístico compartilhado em memória indexado por tenantId (fallback offline)
_BANCO_TERCEIRIZADAS: dict[str, dict[str, dict[str, Any]]] = {
    "tnt_alfa": {
        "ter_alfa_1": {
            "id": "ter_alfa_1",
            "razaoSocial": "Alfa Serviços de Manutenção Ltda",
            "cnpj": "11111111000101",
            "ativa": True,
        },
        "ter_alfa_2": {
            "id": "ter_alfa_2",
            "razaoSocial": "Alfa Construções e Reformas SA",
            "cnpj": "11222333000199",
            "ativa": True,
        },
    },
    "tnt_beta": {
        "ter_beta_1": {
            "id": "ter_beta_1",
            "razaoSocial": "Beta Engenharia e Obras Ltda",
            "cnpj": "22222222000102",
            "ativa": True,
        },
        "ter_beta_2": {
            "id": "ter_beta_2",
            "razaoSocial": "Beta Instalações Industriais Ltda",
            "cnpj": "22333444000188",
            "ativa": True,
        },
    },
}

_BANCO_TIPOS_DOCUMENTO: dict[str, dict[str, dict[str, Any]]] = {
    "tnt_alfa": {
        "tip_aso": {
            "id": "tip_aso",
            "nome": "Atestado de Saúde Ocupacional (ASO)",
            "validadeMeses": 12,
        },
        "tip_nr10": {"id": "tip_nr10", "nome": "Certificado NR-10 Básico", "validadeMeses": 24},
        "tip_nr35": {
            "id": "tip_nr35",
            "nome": "Certificado NR-35 Trabalho em Altura",
            "validadeMeses": 24,
        },
    },
    "tnt_beta": {
        "tip_aso": {
            "id": "tip_aso",
            "nome": "Atestado de Saúde Ocupacional (ASO)",
            "validadeMeses": 12,
        },
        "tip_nr10": {"id": "tip_nr10", "nome": "Certificado NR-10 Básico", "validadeMeses": 24},
    },
}

_BANCO_CONTRATOS: dict[str, dict[str, dict[str, Any]]] = {
    "tnt_alfa": {
        "ctr_alfa_1": {
            "id": "ctr_alfa_1",
            "identificador": "CTR-2026-ALFA-01",
            "terceirizadaId": "ter_alfa_1",
            "dataInicio": "2026-01-01",
            "dataFim": "2026-12-31",
            "vigente": True,
            "versaoMatriz": 1,
            "exigencias": ["tip_aso", "tip_nr10", "tip_nr35"],
        }
    },
    "tnt_beta": {
        "ctr_beta_1": {
            "id": "ctr_beta_1",
            "identificador": "CTR-2026-BETA-01",
            "terceirizadaId": "ter_beta_1",
            "dataInicio": "2026-02-01",
            "dataFim": "2026-12-31",
            "vigente": True,
            "versaoMatriz": 1,
            "exigencias": ["tip_aso", "tip_nr10"],
        }
    },
}


class CadastrosRepositorio:
    """Repositório de cadastros com escopo estrito de tenant e persistência em Firestore."""

    def __init__(self, tenant_id: str, db: FirestoreClient | None = None) -> None:
        self.tenant_id = tenant_id
        self.db = db if db is not None else obter_firestore_client()

    def _tenant_ref(self) -> Any:
        if self.db is None:
            return None
        return self.db.collection("tenants").document(self.tenant_id)

    # -------------------------------------------------------------------------
    # Terceirizadas
    # -------------------------------------------------------------------------
    def listar_terceirizadas(
        self, limite: int = 20, cursor: str | None = None
    ) -> tuple[list[Terceirizada], str | None]:
        if self.db is not None:
            col = self._tenant_ref().collection("terceirizadas")
            query = col.order_by("id")
            todos = [d.to_dict() for d in query.stream()]
        else:
            tenant_map = _BANCO_TERCEIRIZADAS.setdefault(self.tenant_id, {})
            todos = sorted(tenant_map.values(), key=lambda t: str(t["id"]))

        inicio = 0
        if cursor:
            for i, item in enumerate(todos):
                if item["id"] == cursor:
                    inicio = i + 1
                    break

        itens_fatia = todos[inicio : inicio + limite]
        proximo_cursor = itens_fatia[-1]["id"] if inicio + limite < len(todos) else None

        resultado = [
            Terceirizada(
                id=t["id"],
                razaoSocial=t["razaoSocial"],
                cnpj=t["cnpj"],
                ativa=bool(t.get("ativa", True)),
            )
            for t in itens_fatia
        ]
        return resultado, proximo_cursor

    def obter_terceirizada(self, terceirizada_id: str) -> Terceirizada | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("terceirizadas").document(terceirizada_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
        else:
            tenant_map = _BANCO_TERCEIRIZADAS.get(self.tenant_id, {})
            dados = tenant_map.get(terceirizada_id)

        if not dados:
            return None
        return Terceirizada(
            id=dados["id"],
            razaoSocial=dados["razaoSocial"],
            cnpj=dados["cnpj"],
            ativa=bool(dados.get("ativa", True)),
        )

    def criar_terceirizada(self, nova: NovaTerceirizada) -> Terceirizada:
        ter_id = f"ter_{uuid.uuid4().hex[:8]}"
        registro = {
            "id": ter_id,
            "razaoSocial": nova.razaoSocial,
            "cnpj": nova.cnpj,
            "ativa": True,
            "criadoEm": datetime.datetime.now(UTC).isoformat(),
        }

        if self.db is not None:
            self._tenant_ref().collection("terceirizadas").document(ter_id).set(registro)
        else:
            tenant_map = _BANCO_TERCEIRIZADAS.setdefault(self.tenant_id, {})
            tenant_map[ter_id] = registro

        return Terceirizada(
            id=ter_id,
            razaoSocial=nova.razaoSocial,
            cnpj=nova.cnpj,
            ativa=True,
        )

    # -------------------------------------------------------------------------
    # Tipos de Documento
    # -------------------------------------------------------------------------
    def listar_tipos_documento(self) -> list[TipoDocumento]:
        if self.db is not None:
            col = self._tenant_ref().collection("tiposDocumento")
            todos = [d.to_dict() for d in col.order_by("nome").stream()]
        else:
            tenant_map = _BANCO_TIPOS_DOCUMENTO.setdefault(self.tenant_id, {})
            todos = sorted(tenant_map.values(), key=lambda t: str(t["nome"]))

        return [
            TipoDocumento(
                id=td["id"],
                nome=td["nome"],
                validadeMeses=int(td["validadeMeses"]),
            )
            for td in todos
        ]

    def obter_tipo_documento(self, tipo_id: str) -> TipoDocumento | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("tiposDocumento").document(tipo_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
        else:
            tenant_map = _BANCO_TIPOS_DOCUMENTO.get(self.tenant_id, {})
            dados = tenant_map.get(tipo_id)

        if not dados:
            return None
        return TipoDocumento(
            id=dados["id"],
            nome=dados["nome"],
            validadeMeses=int(dados["validadeMeses"]),
        )

    def criar_tipo_documento(self, novo: NovoTipoDocumento) -> TipoDocumento:
        tipo_id = f"tip_{uuid.uuid4().hex[:8]}"
        registro = {
            "id": tipo_id,
            "nome": novo.nome,
            "validadeMeses": novo.validadeMeses,
            "ativo": True,
            "criadoEm": datetime.datetime.now(UTC).isoformat(),
        }

        if self.db is not None:
            self._tenant_ref().collection("tiposDocumento").document(tipo_id).set(registro)
        else:
            tenant_map = _BANCO_TIPOS_DOCUMENTO.setdefault(self.tenant_id, {})
            tenant_map[tipo_id] = registro

        return TipoDocumento(
            id=tipo_id,
            nome=novo.nome,
            validadeMeses=novo.validadeMeses,
        )

    # -------------------------------------------------------------------------
    # Contratos
    # -------------------------------------------------------------------------
    def _montar_contrato(self, dados: dict[str, Any]) -> Contrato:
        exigencias_obj: list[TipoDocumento] = []
        for tid in dados.get("exigencias", []):
            tipo = self.obter_tipo_documento(tid)
            if tipo:
                exigencias_obj.append(tipo)

        return Contrato(
            id=dados["id"],
            identificador=dados["identificador"],
            terceirizadaId=dados["terceirizadaId"],
            dataInicio=dados["dataInicio"],
            dataFim=dados["dataFim"],
            vigente=dados["vigente"],
            versaoMatriz=dados["versaoMatriz"],
            exigencias=exigencias_obj,
        )

    def listar_contratos(
        self,
        terceirizada_id_filtro: str | None = None,
        limite: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Contrato], str | None]:
        if self.db is not None:
            col = self._tenant_ref().collection("contratos")
            if terceirizada_id_filtro:
                query = col.where("terceirizadaId", "==", terceirizada_id_filtro).order_by("id")
            else:
                query = col.order_by("id")
            todos = [d.to_dict() for d in query.stream()]
        else:
            tenant_map = _BANCO_CONTRATOS.setdefault(self.tenant_id, {})
            todos = sorted(tenant_map.values(), key=lambda c: str(c["id"]))
            if terceirizada_id_filtro:
                todos = [c for c in todos if c["terceirizadaId"] == terceirizada_id_filtro]

        inicio = 0
        if cursor:
            for i, item in enumerate(todos):
                if item["id"] == cursor:
                    inicio = i + 1
                    break

        itens_fatia = todos[inicio : inicio + limite]
        proximo_cursor = itens_fatia[-1]["id"] if inicio + limite < len(todos) else None

        resultado = [self._montar_contrato(c) for c in itens_fatia]
        return resultado, proximo_cursor

    def obter_contrato(
        self, contrato_id: str, terceirizada_id_filtro: str | None = None
    ) -> Contrato | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("contratos").document(contrato_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
            if not dados:
                return None
            if terceirizada_id_filtro and dados.get("terceirizadaId") != terceirizada_id_filtro:
                return None
            return self._montar_contrato(dados)

        tenant_map = _BANCO_CONTRATOS.get(self.tenant_id, {})
        dados_mem = tenant_map.get(contrato_id)
        if not dados_mem:
            return None
        if terceirizada_id_filtro and dados_mem["terceirizadaId"] != terceirizada_id_filtro:
            return None
        return self._montar_contrato(dados_mem)

    def criar_contrato(self, novo: NovoContrato) -> Contrato:
        ctr_id = f"ctr_{uuid.uuid4().hex[:8]}"
        registro = {
            "id": ctr_id,
            "identificador": novo.identificador,
            "terceirizadaId": novo.terceirizadaId,
            "dataInicio": novo.dataInicio,
            "dataFim": novo.dataFim,
            "vigente": True,
            "versaoMatriz": 0,
            "exigencias": [],
            "criadoEm": datetime.datetime.now(UTC).isoformat(),
        }

        if self.db is not None:
            self._tenant_ref().collection("contratos").document(ctr_id).set(registro)
        else:
            tenant_map = _BANCO_CONTRATOS.setdefault(self.tenant_id, {})
            tenant_map[ctr_id] = registro

        return self._montar_contrato(registro)

    def substituir_exigencias(
        self, contrato_id: str, tipo_documento_ids: list[str]
    ) -> Contrato | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("contratos").document(contrato_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
            if not dados:
                return None

            novas_exigencias = list(dict.fromkeys(tipo_documento_ids))
            nova_versao = int(dados.get("versaoMatriz", 0)) + 1
            doc_ref.update({"exigencias": novas_exigencias, "versaoMatriz": nova_versao})
            dados["exigencias"] = novas_exigencias
            dados["versaoMatriz"] = nova_versao
            return self._montar_contrato(dados)

        tenant_map = _BANCO_CONTRATOS.get(self.tenant_id, {})
        dados_mem = tenant_map.get(contrato_id)
        if not dados_mem:
            return None

        dados_mem["exigencias"] = list(dict.fromkeys(tipo_documento_ids))
        dados_mem["versaoMatriz"] = dados_mem.get("versaoMatriz", 0) + 1
        return self._montar_contrato(dados_mem)
