"""Repositório de dados para J2 e J3 com persistência no Cloud Firestore.

Conforme a ADR 0002 e AGENTS.md:
- Escopo restrito ao tenantId injetado no construtor (tenants/{tenantId}/...).
- Controle de acesso intra-tenant para terceirizadas.
- Integração direta com Firestore quando disponível (emuladores/produção).
- Mantém fallback para memória determinística em testes unitários offline.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import re
import uuid
from typing import Any

from google.cloud.firestore import Client as FirestoreClient

from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.comum.erros import conflito, recurso_nao_encontrado
from habilita.comum.firestore import obter_firestore_client
from habilita.config import get_settings
from habilita.trabalhadores.schemas import (
    Alocacao,
    ArquivoMetadados,
    Documento,
    Pendencia,
    Trabalhador,
    TrabalhadorDetalhe,
    UrlDownloadResponse,
)

UTC = getattr(datetime, "UTC", datetime.timezone.utc)  # noqa: UP017

_BANCO_TRABALHADORES: dict[str, dict[str, dict[str, Any]]] = {
    "tnt_alfa": {
        "trb_alfa_1": {
            "id": "trb_alfa_1",
            "terceirizadaId": "ter_alfa_1",
            "nome": "Carlos Eduardo Silva",
            "cpfMascarado": "***.000.001-**",
            "cpfHash": "255ec8cc69a7437e703380b44b82e2dabbb25060b2dec5114ae4e0a2e3a1417a",
            "ativo": True,
            "alocacoes": ["ctr_alfa_1"],
        },
        "trb_alfa_y1": {
            "id": "trb_alfa_y1",
            "terceirizadaId": "ter_alfa_2",
            "nome": "Yuri Ramos",
            "cpfMascarado": "***.000.002-**",
            "cpfHash": "100c1b2626b88b40c2e4bccdadada5e8cb7c74054ecbda887c4c7eb26359f8cc",
            "ativo": True,
            "alocacoes": [],
        },
    },
    "tnt_beta": {
        "trb_beta_1": {
            "id": "trb_beta_1",
            "terceirizadaId": "ter_beta_1",
            "nome": "Alexandre Pires Barbosa",
            "cpfMascarado": "***.000.001-**",
            "cpfHash": "4d564a58e9d608d7b4922a550b9ae2371a21dd7e6f883f3b3c290849de02b526",
            "ativo": True,
            "alocacoes": ["ctr_beta_1"],
        }
    },
}

_BANCO_DOCUMENTOS: dict[str, dict[str, dict[str, Any]]] = {
    "tnt_alfa": {
        "doc_alfa_1_aso": {
            "id": "doc_alfa_1_aso",
            "trabalhadorId": "trb_alfa_1",
            "tipoDocumentoId": "tip_aso",
            "validoAte": "2027-01-01",
            "statusRevisao": "APROVADO",
            "enviadoEm": "2026-10-15T10:00:00Z",
            "dataEmissao": "2026-01-01",
            "revisadoEm": "2026-10-16T14:00:00Z",
            "motivoRejeicao": None,
            "situacaoValidade": "VALIDO",
            "arquivo": {
                "contentType": "application/pdf",
                "tamanhoBytes": 1024,
                "sha256": hashlib.sha256(b"conteudo-ficticio-alfa-1").hexdigest(),
            },
        }
    },
    "tnt_beta": {
        "doc_beta_1_aso": {
            "id": "doc_beta_1_aso",
            "trabalhadorId": "trb_beta_1",
            "tipoDocumentoId": "tip_aso",
            "validoAte": "2027-02-01",
            "statusRevisao": "PENDENTE",
            "enviadoEm": "2026-10-16T11:00:00Z",
            "dataEmissao": "2026-02-01",
            "revisadoEm": None,
            "motivoRejeicao": None,
            "situacaoValidade": "VALIDO",
            "arquivo": {
                "contentType": "application/pdf",
                "tamanhoBytes": 2048,
                "sha256": hashlib.sha256(b"conteudo-ficticio-beta-1").hexdigest(),
            },
        }
    },
}


class TrabalhadoresRepositorio:
    """Repositório isolado por tenant com persistência no Firestore."""

    def __init__(self, tenant_id: str, db: FirestoreClient | None = None) -> None:
        self.tenant_id = tenant_id
        self.db = db if db is not None else obter_firestore_client()

    def _tenant_ref(self) -> Any:
        if self.db is None:
            return None
        return self.db.collection("tenants").document(self.tenant_id)

    # -------------------------------------------------------------------------
    # Trabalhadores
    # -------------------------------------------------------------------------
    def listar_trabalhadores(
        self,
        terceirizada_id_filtro: str | None = None,
        limite: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Trabalhador], str | None]:
        if self.db is not None:
            col = self._tenant_ref().collection("trabalhadores")
            if terceirizada_id_filtro:
                query = col.where("terceirizadaId", "==", terceirizada_id_filtro).order_by("id")
            else:
                query = col.order_by("id")
            todos = [d.to_dict() for d in query.stream()]
        else:
            tenant_map = _BANCO_TRABALHADORES.setdefault(self.tenant_id, {})
            todos = sorted(tenant_map.values(), key=lambda t: str(t["id"]))
            if terceirizada_id_filtro:
                todos = [t for t in todos if t["terceirizadaId"] == terceirizada_id_filtro]

        inicio = 0
        if cursor:
            for i, item in enumerate(todos):
                if item["id"] == cursor:
                    inicio = i + 1
                    break

        itens_fatia = todos[inicio : inicio + limite]
        proximo_cursor = itens_fatia[-1]["id"] if inicio + limite < len(todos) else None

        resultado = [
            Trabalhador(
                id=t["id"],
                terceirizadaId=t["terceirizadaId"],
                nome=t["nome"],
                cpfMascarado=t["cpfMascarado"],
                ativo=t["ativo"],
            )
            for t in itens_fatia
        ]
        return resultado, proximo_cursor

    def obter_trabalhador(
        self, trabalhador_id: str, terceirizada_id_filtro: str | None = None
    ) -> TrabalhadorDetalhe | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
            if not dados:
                return None
            if terceirizada_id_filtro and dados.get("terceirizadaId") != terceirizada_id_filtro:
                return None

            # Documentos do trabalhador do Firestore
            docs_query = (
                self._tenant_ref()
                .collection("documentos")
                .where("trabalhadorId", "==", trabalhador_id)
            )
            docs_trb = [Documento(**d.to_dict()) for d in docs_query.stream()]

            # Alocações com semáforo dinâmico
            alocs_trb = [
                self.calcular_semaforo(ctr_id, trabalhador_id)
                for ctr_id in dados.get("alocacoes", [])
            ]

            return TrabalhadorDetalhe(
                id=dados["id"],
                terceirizadaId=dados["terceirizadaId"],
                nome=dados["nome"],
                cpfMascarado=dados["cpfMascarado"],
                ativo=dados["ativo"],
                documentos=docs_trb,
                alocacoes=alocs_trb,
            )

        tenant_map = _BANCO_TRABALHADORES.get(self.tenant_id, {})
        dados_mem = tenant_map.get(trabalhador_id)
        if not dados_mem:
            return None
        if terceirizada_id_filtro and dados_mem["terceirizadaId"] != terceirizada_id_filtro:
            return None

        docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
        docs_trb_mem = [
            Documento(**d) for d in docs_map.values() if d.get("trabalhadorId") == trabalhador_id
        ]

        alocs_trb_mem = [
            self.calcular_semaforo(ctr_id, trabalhador_id)
            for ctr_id in dados_mem.get("alocacoes", [])
        ]

        return TrabalhadorDetalhe(
            id=dados_mem["id"],
            terceirizadaId=dados_mem["terceirizadaId"],
            nome=dados_mem["nome"],
            cpfMascarado=dados_mem["cpfMascarado"],
            ativo=dados_mem["ativo"],
            documentos=docs_trb_mem,
            alocacoes=alocs_trb_mem,
        )

    def criar_trabalhador(self, nome: str, cpf: str, terceirizada_id: str) -> Trabalhador:
        trb_id = f"trb_{uuid.uuid4().hex[:8]}"
        cpf_limpo = re.sub(r"\D", "", cpf)
        cpf_mascarado = f"***.{cpf_limpo[3:6]}.{cpf_limpo[6:9]}-**"
        chave_hmac = get_settings().cpf_hmac_key.encode("utf-8")
        cpf_hash = hmac.new(chave_hmac, cpf_limpo.encode("utf-8"), hashlib.sha256).hexdigest()

        if self.db is not None:
            indice_ref = self._tenant_ref().collection("indiceCpf").document(cpf_hash)
            if indice_ref.get().exists:
                raise conflito("CPF já cadastrado no tenant.")

            registro = {
                "id": trb_id,
                "terceirizadaId": terceirizada_id,
                "nome": nome,
                "cpfHash": cpf_hash,
                "cpfMascarado": cpf_mascarado,
                "ativo": True,
                "alocacoes": [],
                "criadoEm": datetime.datetime.now(UTC).isoformat(),
            }
            self._tenant_ref().collection("trabalhadores").document(trb_id).set(registro)
            indice_ref.set({"trabalhadorId": trb_id})
        else:
            tenant_map = _BANCO_TRABALHADORES.setdefault(self.tenant_id, {})
            for existente in tenant_map.values():
                if existente.get("cpfHash") == cpf_hash:
                    raise conflito("CPF já cadastrado no tenant.")

            registro = {
                "id": trb_id,
                "terceirizadaId": terceirizada_id,
                "nome": nome,
                "cpfHash": cpf_hash,
                "cpfMascarado": cpf_mascarado,
                "ativo": True,
                "alocacoes": [],
                "criadoEm": datetime.datetime.now(UTC).isoformat(),
            }
            tenant_map[trb_id] = registro

        return Trabalhador(
            id=trb_id,
            terceirizadaId=terceirizada_id,
            nome=nome,
            cpfMascarado=cpf_mascarado,
            ativo=True,
        )

    def calcular_semaforo(
        self,
        contrato_id: str,
        trabalhador_id: str,
        data_referencia: datetime.date | None = None,
    ) -> Alocacao:
        """Calcula dinamicamente o semáforo de SST para uma alocação (RN-04 a RN-07)."""
        cad_repo = CadastrosRepositorio(tenant_id=self.tenant_id, db=self.db)
        contrato = cad_repo.obter_contrato(contrato_id)

        hoje = data_referencia or datetime.datetime.now(UTC).date()
        agora = datetime.datetime.now(UTC).isoformat()

        if not contrato or not contrato.exigencias:
            return Alocacao(
                contratoId=contrato_id,
                trabalhadorId=trabalhador_id,
                semaforo="VERDE",
                pendencias=[],
                calculadoEm=agora,
                proximoVencimento=None,
            )

        if self.db is not None:
            docs_query = (
                self._tenant_ref()
                .collection("documentos")
                .where("trabalhadorId", "==", trabalhador_id)
                .where("statusRevisao", "==", "APROVADO")
            )
            docs_trb = [d.to_dict() for d in docs_query.stream()]
        else:
            docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
            docs_trb = [
                d
                for d in docs_map.values()
                if d.get("trabalhadorId") == trabalhador_id and d.get("statusRevisao") == "APROVADO"
            ]

        pendencias: list[Pendencia] = []
        situacoes: list[str] = []
        vencimentos_validos: list[str] = []

        for ex in contrato.exigencias:
            tipo_id = ex.id
            tipo_nome = ex.nome

            docs_tipo = [d for d in docs_trb if d.get("tipoDocumentoId") == tipo_id]
            if not docs_tipo:
                pendencias.append(
                    Pendencia(
                        tipoDocumentoId=tipo_id,
                        tipoNome=tipo_nome,
                        motivo="AUSENTE",
                    )
                )
                situacoes.append("AUSENTE")
                continue

            doc_eleito = max(docs_tipo, key=lambda d: str(d.get("validoAte", "")))
            valido_ate_str = str(doc_eleito.get("validoAte", ""))
            try:
                valido_ate_date = datetime.date.fromisoformat(valido_ate_str)
                dias = (valido_ate_date - hoje).days
            except ValueError:
                dias = -1

            if dias < 0:
                situacao = "VENCIDO"
                pendencias.append(
                    Pendencia(
                        tipoDocumentoId=tipo_id,
                        tipoNome=tipo_nome,
                        motivo="VENCIDO",
                    )
                )
            elif dias <= 30:
                situacao = "A_VENCER"
                vencimentos_validos.append(valido_ate_str)
            else:
                situacao = "VALIDO"
                vencimentos_validos.append(valido_ate_str)

            situacoes.append(situacao)

        if any(s in ("AUSENTE", "VENCIDO") for s in situacoes):
            semaforo = "VERMELHO"
        elif any(s == "A_VENCER" for s in situacoes):
            semaforo = "AMARELO"
        else:
            semaforo = "VERDE"

        proximo_vencimento = min(vencimentos_validos) if vencimentos_validos else None

        return Alocacao(
            contratoId=contrato_id,
            trabalhadorId=trabalhador_id,
            semaforo=semaforo,
            pendencias=pendencias,
            calculadoEm=agora,
            proximoVencimento=proximo_vencimento,
        )

    def vincular_alocacao(self, contrato_id: str, trabalhador_id: str) -> Alocacao:
        if self.db is not None:
            trb_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            snap = trb_ref.get()
            if snap.exists:
                dados = snap.to_dict() or {}
                alocs = list(dados.get("alocacoes", []))
                if contrato_id not in alocs:
                    alocs.append(contrato_id)
                    trb_ref.update({"alocacoes": alocs})
        else:
            tenant_map = _BANCO_TRABALHADORES.setdefault(self.tenant_id, {})
            trb = tenant_map.get(trabalhador_id)
            if trb and contrato_id not in trb.get("alocacoes", []):
                trb.setdefault("alocacoes", []).append(contrato_id)

        return self.calcular_semaforo(contrato_id, trabalhador_id)

    # -------------------------------------------------------------------------
    # Documentos
    # -------------------------------------------------------------------------
    def listar_documentos(
        self,
        status_revisao: str | None = None,
        terceirizada_id_filtro: str | None = None,
        limite: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Documento], str | None]:
        if self.db is not None:
            col = self._tenant_ref().collection("documentos")
            if status_revisao:
                col = col.where("statusRevisao", "==", status_revisao)
            todos = [d.to_dict() for d in col.order_by("id").stream()]

            if terceirizada_id_filtro:
                trb_col = self._tenant_ref().collection("trabalhadores")
                trbs = trb_col.where("terceirizadaId", "==", terceirizada_id_filtro).stream()
                trb_ids = {t.id for t in trbs}
                todos = [d for d in todos if d.get("trabalhadorId") in trb_ids]
        else:
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            todos = sorted(docs_map.values(), key=lambda d: str(d["id"]))

            if status_revisao:
                todos = [d for d in todos if d.get("statusRevisao") == status_revisao]

            if terceirizada_id_filtro:
                trb_map = _BANCO_TRABALHADORES.get(self.tenant_id, {})
                trb_ids_terc = {
                    tid
                    for tid, trb in trb_map.items()
                    if trb["terceirizadaId"] == terceirizada_id_filtro
                }
                todos = [d for d in todos if d.get("trabalhadorId") in trb_ids_terc]

        inicio = 0
        if cursor:
            for i, item in enumerate(todos):
                if item["id"] == cursor:
                    inicio = i + 1
                    break

        itens_fatia = todos[inicio : inicio + limite]
        proximo_cursor = itens_fatia[-1]["id"] if inicio + limite < len(todos) else None

        resultado = [Documento(**d) for d in itens_fatia]
        return resultado, proximo_cursor

    def obter_documento(
        self, documento_id: str, terceirizada_id_filtro: str | None = None
    ) -> Documento | None:
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            dados = snap.to_dict()
            if not dados:
                return None

            if terceirizada_id_filtro:
                trb_ref = (
                    self._tenant_ref()
                    .collection("trabalhadores")
                    .document(dados.get("trabalhadorId", ""))
                )
                trb_snap = trb_ref.get()
                if (
                    not trb_snap.exists
                    or trb_snap.to_dict().get("terceirizadaId") != terceirizada_id_filtro
                ):
                    return None

            return Documento(**dados)

        docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
        dados_mem = docs_map.get(documento_id)
        if not dados_mem:
            return None

        if terceirizada_id_filtro:
            trb_map = _BANCO_TRABALHADORES.get(self.tenant_id, {})
            trb = trb_map.get(dados_mem.get("trabalhadorId", ""))
            if not trb or trb.get("terceirizadaId") != terceirizada_id_filtro:
                return None

        return Documento(**dados_mem)

    def criar_documento(
        self,
        trabalhador_id: str,
        tipo_documento_id: str,
        valido_ate: str,
        data_emissao: str | None,
        arquivo_meta: ArquivoMetadados,
    ) -> Documento:
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        agora = datetime.datetime.now(UTC).isoformat()

        registro = {
            "id": doc_id,
            "trabalhadorId": trabalhador_id,
            "tipoDocumentoId": tipo_documento_id,
            "validoAte": valido_ate,
            "statusRevisao": "PENDENTE",
            "enviadoEm": agora,
            "dataEmissao": data_emissao,
            "revisadoEm": None,
            "motivoRejeicao": None,
            "situacaoValidade": "VALIDO",
            "arquivo": arquivo_meta.model_dump(),
        }

        if self.db is not None:
            self._tenant_ref().collection("documentos").document(doc_id).set(registro)
            trb_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            trb_ref.collection("documentos").document(doc_id).set(registro)
        else:
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            docs_map[doc_id] = registro

        return Documento(**registro)

    def aprovar_documento(self, documento_id: str) -> Documento:
        agora = datetime.datetime.now(UTC).isoformat()
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            snap = doc_ref.get()
            if not snap.exists:
                raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")
            dados = snap.to_dict() or {}
            if dados.get("statusRevisao") != "PENDENTE":
                raise conflito("Apenas documentos com status PENDENTE podem ser aprovados.")

            atualizacao = {
                "statusRevisao": "APROVADO",
                "revisadoEm": agora,
                "motivoRejeicao": None,
            }
            doc_ref.update(atualizacao)
            dados.update(atualizacao)
            return Documento(**dados)

        docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
        dados_mem = docs_map.get(documento_id)
        if not dados_mem:
            raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")

        if dados_mem.get("statusRevisao") != "PENDENTE":
            raise conflito("Apenas documentos com status PENDENTE podem ser aprovados.")

        dados_mem["statusRevisao"] = "APROVADO"
        dados_mem["revisadoEm"] = agora
        dados_mem["motivoRejeicao"] = None
        return Documento(**dados_mem)

    def rejeitar_documento(self, documento_id: str, motivo: str) -> Documento:
        agora = datetime.datetime.now(UTC).isoformat()
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            snap = doc_ref.get()
            if not snap.exists:
                raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")
            dados = snap.to_dict() or {}
            if dados.get("statusRevisao") != "PENDENTE":
                raise conflito("Apenas documentos com status PENDENTE podem ser rejeitados.")

            atualizacao = {
                "statusRevisao": "REJEITADO",
                "revisadoEm": agora,
                "motivoRejeicao": motivo,
            }
            doc_ref.update(atualizacao)
            dados.update(atualizacao)
            return Documento(**dados)

        docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
        dados_mem = docs_map.get(documento_id)
        if not dados_mem:
            raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")

        if dados_mem.get("statusRevisao") != "PENDENTE":
            raise conflito("Apenas documentos com status PENDENTE podem ser rejeitados.")

        dados_mem["statusRevisao"] = "REJEITADO"
        dados_mem["revisadoEm"] = agora
        dados_mem["motivoRejeicao"] = motivo
        return Documento(**dados_mem)

    def gerar_url_download(
        self, documento_id: str, terceirizada_id_filtro: str | None = None
    ) -> UrlDownloadResponse | None:
        doc = self.obter_documento(documento_id, terceirizada_id_filtro)
        if not doc:
            return None

        expira = (datetime.datetime.now(UTC) + datetime.timedelta(minutes=15)).isoformat()
        url = f"http://127.0.0.1:9199/download/storage/v1/b/demo-habilita.appspot.com/o/tenants%2F{self.tenant_id}%2Fdocumentos%2F{documento_id}.pdf?alt=media"
        return UrlDownloadResponse(url=url, expiraEm=expira)
