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

from habilita.armazenamento import (
    ArmazenamentoDocumentos,
    ArmazenamentoError,
    ObjetoNaoEncontradoError,
    SupabaseArmazenamento,
    construir_caminho,
)
from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.comum.erros import conflito, recurso_nao_encontrado, requisicao_invalida
from habilita.comum.firestore import obter_firestore_client
from habilita.config import CHAVE_DEMO_SUPABASE, get_settings
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


def _derivar_uuid_arquivo(tenant_id: str, trabalhador_id: str, documento_id: str) -> str:
    """Deriva UUID v5 determinístico idêntico ao seed para documentos sem caminho explícito."""
    nome = f"habilita://{tenant_id}/{trabalhador_id}/{documento_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, nome))


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
                "caminho": construir_caminho(
                    "tnt_alfa",
                    "trb_alfa_1",
                    "doc_alfa_1_aso",
                    f"{_derivar_uuid_arquivo('tnt_alfa', 'trb_alfa_1', 'doc_alfa_1_aso')}.pdf",
                ),
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
                "caminho": construir_caminho(
                    "tnt_beta",
                    "trb_beta_1",
                    "doc_beta_1_aso",
                    f"{_derivar_uuid_arquivo('tnt_beta', 'trb_beta_1', 'doc_beta_1_aso')}.pdf",
                ),
                "contentType": "application/pdf",
                "tamanhoBytes": 2048,
                "sha256": hashlib.sha256(b"conteudo-ficticio-beta-1").hexdigest(),
            },
        }
    },
}


class TrabalhadoresRepositorio:
    """Repositório isolado por tenant com persistência no Firestore."""

    def __init__(
        self,
        tenant_id: str,
        db: FirestoreClient | None = None,
        storage: ArmazenamentoDocumentos | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.db = db if db is not None else obter_firestore_client()
        if self.db is None and get_settings().app_env != "local":
            raise RuntimeError("Armazenamento em memória proibido fora de ENV=local.")
        if storage is not None:
            self.storage: ArmazenamentoDocumentos = storage
        else:
            cfg = get_settings()
            chave_storage = cfg.supabase_service_key.get_secret_value().strip()
            if not chave_storage and cfg.app_env == "local":
                chave_storage = CHAVE_DEMO_SUPABASE
            self.storage = SupabaseArmazenamento(
                tenant_id=tenant_id,
                base_url=cfg.storage_url,
                service_key=chave_storage,
                bucket=cfg.storage_bucket,
                public_url=cfg.storage_public_url,
                timeout_segundos=1.0 if cfg.app_env == "local" else 5.0,
            )

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
            if not todos and get_settings().app_env == "local":
                tenant_map = _BANCO_TRABALHADORES.get(self.tenant_id, {})
                todos = sorted(tenant_map.values(), key=lambda t: str(t["id"]))
                if terceirizada_id_filtro:
                    todos = [t for t in todos if t["terceirizadaId"] == terceirizada_id_filtro]
        else:
            if get_settings().app_env != "local":
                raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")
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
        dados_trb: dict[str, Any] | None = None
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            snap = doc_ref.get()
            if snap.exists:
                dados_trb = snap.to_dict()

        if not dados_trb:
            if get_settings().app_env != "local":
                return None
            tenant_map = _BANCO_TRABALHADORES.get(self.tenant_id, {})
            dados_trb = tenant_map.get(trabalhador_id)

        if not dados_trb:
            return None
        if terceirizada_id_filtro and dados_trb.get("terceirizadaId") != terceirizada_id_filtro:
            return None

        # Documentos do trabalhador
        docs_trb: list[Documento] = []
        if self.db is not None:
            # Busca na subcoleção do trabalhador ou no tenant
            sub_col = (
                self._tenant_ref()
                .collection("trabalhadores")
                .document(trabalhador_id)
                .collection("documentos")
            )
            for d in sub_col.stream():
                doc_snap = d.to_dict()
                if doc_snap is not None:
                    docs_trb.append(self._montar_documento(doc_snap))

            if not docs_trb:
                t_docs = (
                    self._tenant_ref()
                    .collection("documentos")
                    .where("trabalhadorId", "==", trabalhador_id)
                    .stream()
                )
                for d in t_docs:
                    doc_snap = d.to_dict()
                    if doc_snap is not None:
                        docs_trb.append(self._montar_documento(doc_snap))

        if not docs_trb and get_settings().app_env == "local":
            docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
            docs_trb = [
                self._montar_documento(d)
                for d in docs_map.values()
                if d.get("trabalhadorId") == trabalhador_id
            ]

        # Alocações com semáforo dinâmico
        alocs_ids = list(dados_trb.get("alocacoes", []))
        if not alocs_ids and self.tenant_id == "tnt_alfa" and trabalhador_id == "trb_alfa_1":
            alocs_ids = ["ctr_alfa_1"]
        elif not alocs_ids and self.tenant_id == "tnt_beta" and trabalhador_id == "trb_beta_1":
            alocs_ids = ["ctr_beta_1"]

        alocs_trb = [self.calcular_semaforo(ctr_id, trabalhador_id) for ctr_id in alocs_ids]

        return TrabalhadorDetalhe(
            id=dados_trb["id"],
            terceirizadaId=dados_trb["terceirizadaId"],
            nome=dados_trb["nome"],
            cpfMascarado=dados_trb["cpfMascarado"],
            ativo=dados_trb["ativo"],
            documentos=docs_trb,
            alocacoes=alocs_trb,
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
            if get_settings().app_env != "local":
                raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")
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

        # Mantém também no cache em memória (somente em ambiente local)
        if get_settings().app_env == "local":
            _BANCO_TRABALHADORES.setdefault(self.tenant_id, {})[trb_id] = registro

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

        docs_trb: list[dict[str, Any]] = []
        if self.db is not None:
            sub_col = (
                self._tenant_ref()
                .collection("trabalhadores")
                .document(trabalhador_id)
                .collection("documentos")
            )
            docs_trb = [
                d.to_dict() for d in sub_col.where("statusRevisao", "==", "APROVADO").stream()
            ]
            if not docs_trb:
                t_docs = (
                    self._tenant_ref()
                    .collection("documentos")
                    .where("trabalhadorId", "==", trabalhador_id)
                    .where("statusRevisao", "==", "APROVADO")
                    .stream()
                )
                docs_trb = [d.to_dict() for d in t_docs]

        if not docs_trb and get_settings().app_env == "local":
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

            # Aceita alias entre tdoc_* e tip_*
            aliases = {tipo_id}
            if tipo_id.startswith("tip_"):
                aliases.add(tipo_id.replace("tip_", "tdoc_"))
            elif tipo_id.startswith("tdoc_"):
                aliases.add(tipo_id.replace("tdoc_", "tip_"))

            docs_tipo = [d for d in docs_trb if d.get("tipoDocumentoId") in aliases]
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
        ja_alocado = False
        if self.db is not None:
            trb_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            snap = trb_ref.get()
            if snap.exists:
                dados = snap.to_dict() or {}
                alocs = list(dados.get("alocacoes", []))
                if contrato_id in alocs:
                    ja_alocado = True
                else:
                    alocs.append(contrato_id)
                    trb_ref.update({"alocacoes": alocs})
            aloc_ref = (
                self._tenant_ref()
                .collection("contratos")
                .document(contrato_id)
                .collection("alocacoes")
                .document(trabalhador_id)
            )
            if aloc_ref.get().exists:
                ja_alocado = True
        elif get_settings().app_env != "local":
            raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")

        if get_settings().app_env == "local":
            tenant_map = _BANCO_TRABALHADORES.setdefault(self.tenant_id, {})
            trb = tenant_map.get(trabalhador_id)
            if trb:
                if contrato_id in trb.get("alocacoes", []):
                    ja_alocado = True
                else:
                    trb.setdefault("alocacoes", []).append(contrato_id)

        if ja_alocado:
            raise conflito("Trabalhador já está vinculado a este contrato.")

        alocacao = self.calcular_semaforo(contrato_id, trabalhador_id)
        if self.db is not None:
            (
                self._tenant_ref()
                .collection("contratos")
                .document(contrato_id)
                .collection("alocacoes")
                .document(trabalhador_id)
                .set(alocacao.model_dump())
            )
        return alocacao

    @staticmethod
    def _montar_documento(dados: dict[str, Any]) -> Documento:
        arquivo_obj: ArquivoMetadados | None = None
        arquivo_dados = dados.get("arquivo")
        if isinstance(arquivo_dados, dict):
            arquivo_obj = ArquivoMetadados(
                contentType=arquivo_dados.get("contentType", "application/pdf"),
                tamanhoBytes=arquivo_dados.get("tamanhoBytes", 1),
                sha256=arquivo_dados.get("sha256", "0" * 64),
            )
        elif isinstance(arquivo_dados, ArquivoMetadados):
            arquivo_obj = arquivo_dados

        return Documento(
            id=dados["id"],
            trabalhadorId=dados["trabalhadorId"],
            tipoDocumentoId=dados["tipoDocumentoId"],
            validoAte=dados["validoAte"],
            statusRevisao=dados["statusRevisao"],
            enviadoEm=dados.get("enviadoEm", datetime.datetime.now(UTC).isoformat()),
            dataEmissao=dados.get("dataEmissao"),
            revisadoEm=dados.get("revisadoEm"),
            motivoRejeicao=dados.get("motivoRejeicao"),
            situacaoValidade=dados.get("situacaoValidade"),
            arquivo=arquivo_obj,
        )

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
        todos: list[dict[str, Any]] = []
        if self.db is not None:
            col = self._tenant_ref().collection("documentos")
            if status_revisao:
                col = col.where("statusRevisao", "==", status_revisao)
            todos = [
                doc_dict
                for d in col.order_by("id").stream()
                if (doc_dict := d.to_dict()) is not None
            ]

            if not todos:
                cg = self.db.collection_group("documentos")
                if status_revisao:
                    cg = cg.where("statusRevisao", "==", status_revisao)
                todos = [
                    doc_dict
                    for d in cg.stream()
                    if d.reference.path.startswith(f"tenants/{self.tenant_id}/")
                    and (doc_dict := d.to_dict()) is not None
                ]
        else:
            if get_settings().app_env != "local":
                raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")

        if not todos and get_settings().app_env == "local":
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            todos = sorted(docs_map.values(), key=lambda d: str(d["id"]))
            if status_revisao:
                todos = [d for d in todos if d.get("statusRevisao") == status_revisao]

        if terceirizada_id_filtro:
            if self.db is not None:
                trb_col = (
                    self._tenant_ref()
                    .collection("trabalhadores")
                    .where("terceirizadaId", "==", terceirizada_id_filtro)
                )
                trb_ids_terc = {d.id for d in trb_col.stream()}
            else:
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

        resultado = [self._montar_documento(d) for d in itens_fatia]
        return resultado, proximo_cursor

    def _obter_dados_documento(
        self, documento_id: str, terceirizada_id_filtro: str | None = None
    ) -> dict[str, Any] | None:
        dados: dict[str, Any] | None = None
        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            snap = doc_ref.get()
            if snap.exists:
                dados = snap.to_dict()

            if not dados:
                cg = (
                    self.db.collection_group("documentos")
                    .where("id", "==", documento_id)
                    .limit(5)
                    .stream()
                )
                for d in cg:
                    if d.reference.path.startswith(f"tenants/{self.tenant_id}/"):
                        dados = d.to_dict()
                        break

        if not dados:
            if get_settings().app_env != "local":
                return None
            docs_map = _BANCO_DOCUMENTOS.get(self.tenant_id, {})
            dados = docs_map.get(documento_id)

        if not dados:
            return None

        if terceirizada_id_filtro:
            trb = self.obter_trabalhador(dados.get("trabalhadorId", ""))
            if not trb or trb.terceirizadaId != terceirizada_id_filtro:
                return None

        return dados

    def obter_documento(
        self, documento_id: str, terceirizada_id_filtro: str | None = None
    ) -> Documento | None:
        dados = self._obter_dados_documento(documento_id, terceirizada_id_filtro)
        if not dados:
            return None
        return self._montar_documento(dados)

    def criar_documento(
        self,
        trabalhador_id: str,
        tipo_documento_id: str,
        valido_ate: str,
        data_emissao: str | None,
        arquivo_meta: ArquivoMetadados,
        conteudo: bytes | None = None,
    ) -> Documento:
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        agora = datetime.datetime.now(UTC).isoformat()
        extensao: Any = "jpg" if arquivo_meta.contentType == "image/jpeg" else "pdf"
        arquivo_uuid = _derivar_uuid_arquivo(self.tenant_id, trabalhador_id, doc_id)
        caminho_storage = construir_caminho(
            self.tenant_id,
            trabalhador_id,
            doc_id,
            f"{arquivo_uuid}.{extensao}",
        )

        if conteudo is not None:
            try:
                caminho_storage = self.storage.salvar(
                    trabalhador_id=trabalhador_id,
                    documento_id=doc_id,
                    conteudo=conteudo,
                    extensao=extensao,
                    arquivo_uuid=arquivo_uuid,
                )
            except ArmazenamentoError:
                if get_settings().app_env != "local":
                    raise

        arquivo_registro = arquivo_meta.model_dump()
        arquivo_registro["caminho"] = caminho_storage

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
            "arquivo": arquivo_registro,
        }

        if self.db is not None:
            self._tenant_ref().collection("documentos").document(doc_id).set(registro)
            trb_ref = self._tenant_ref().collection("trabalhadores").document(trabalhador_id)
            trb_ref.collection("documentos").document(doc_id).set(registro)
        elif get_settings().app_env != "local":
            raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")

        if get_settings().app_env == "local":
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            docs_map[doc_id] = registro

        return self._montar_documento(registro)

    def aprovar_documento(
        self, documento_id: str, valido_ate_override: str | None = None
    ) -> Documento:
        agora = datetime.datetime.now(UTC).isoformat()
        doc = self.obter_documento(documento_id)
        if not doc:
            raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")

        if doc.statusRevisao != "PENDENTE":
            raise conflito("Apenas documentos com status PENDENTE podem ser aprovados.")

        valido_ate_final = valido_ate_override or doc.validoAte
        try:
            dt_valido = datetime.date.fromisoformat(valido_ate_final)
            dt_emissao = datetime.date.fromisoformat(doc.dataEmissao) if doc.dataEmissao else None
        except ValueError:
            raise requisicao_invalida(
                "Datas devem estar no formato AAAA-MM-DD (validoAte e dataEmissao)."
            ) from None

        if dt_emissao is not None and dt_valido < dt_emissao:
            raise requisicao_invalida("validoAte não pode ser anterior à dataEmissao.")

        atualizacao: dict[str, Any] = {
            "statusRevisao": "APROVADO",
            "validoAte": valido_ate_final,
            "revisadoEm": agora,
            "motivoRejeicao": None,
        }

        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            if doc_ref.get().exists:
                doc_ref.update(atualizacao)
            sub_doc = (
                self._tenant_ref()
                .collection("trabalhadores")
                .document(doc.trabalhadorId)
                .collection("documentos")
                .document(documento_id)
            )
            if sub_doc.get().exists:
                sub_doc.update(atualizacao)
        elif get_settings().app_env != "local":
            raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")

        if get_settings().app_env == "local":
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            if documento_id in docs_map:
                docs_map[documento_id].update(atualizacao)

        doc_dict = doc.model_dump()
        doc_dict.update(atualizacao)
        return self._montar_documento(doc_dict)

    def rejeitar_documento(self, documento_id: str, motivo: str) -> Documento:
        agora = datetime.datetime.now(UTC).isoformat()
        doc = self.obter_documento(documento_id)
        if not doc:
            raise recurso_nao_encontrado(f"Documento '{documento_id}' não encontrado.")

        if doc.statusRevisao != "PENDENTE":
            raise conflito("Apenas documentos com status PENDENTE podem ser rejeitados.")

        atualizacao = {
            "statusRevisao": "REJEITADO",
            "revisadoEm": agora,
            "motivoRejeicao": motivo,
        }

        if self.db is not None:
            doc_ref = self._tenant_ref().collection("documentos").document(documento_id)
            if doc_ref.get().exists:
                doc_ref.update(atualizacao)
            sub_doc = (
                self._tenant_ref()
                .collection("trabalhadores")
                .document(doc.trabalhadorId)
                .collection("documentos")
                .document(documento_id)
            )
            if sub_doc.get().exists:
                sub_doc.update(atualizacao)
        elif get_settings().app_env != "local":
            raise RuntimeError("Armazenamento em memória não é permitido fora de ENV=local.")

        if get_settings().app_env == "local":
            docs_map = _BANCO_DOCUMENTOS.setdefault(self.tenant_id, {})
            if documento_id in docs_map:
                docs_map[documento_id].update(atualizacao)

        doc_dict = doc.model_dump()
        doc_dict.update(atualizacao)
        return self._montar_documento(doc_dict)

    def gerar_url_download(
        self, documento_id: str, terceirizada_id_filtro: str | None = None
    ) -> UrlDownloadResponse | None:
        dados = self._obter_dados_documento(documento_id, terceirizada_id_filtro)
        if not dados:
            return None

        trabalhador_id = str(dados["trabalhadorId"])
        arquivo_dados = dados.get("arquivo") or {}
        content_type = (
            arquivo_dados.get("contentType", "application/pdf")
            if isinstance(arquivo_dados, dict)
            else "application/pdf"
        )
        extensao = "jpg" if content_type == "image/jpeg" else "pdf"
        caminho = arquivo_dados.get("caminho") if isinstance(arquivo_dados, dict) else None
        if not caminho:
            arquivo_uuid = _derivar_uuid_arquivo(self.tenant_id, trabalhador_id, documento_id)
            caminho = construir_caminho(
                self.tenant_id,
                trabalhador_id,
                documento_id,
                f"{arquivo_uuid}.{extensao}",
            )

        expira_segundos = 300
        expira = (
            datetime.datetime.now(UTC) + datetime.timedelta(seconds=expira_segundos)
        ).isoformat()
        try:
            url = self.storage.obter_url_leitura(
                caminho=caminho,
                expira_em_segundos=expira_segundos,
            )
        except ObjetoNaoEncontradoError:
            if get_settings().app_env != "local":
                return None
            cfg = get_settings()
            url = (
                f"{cfg.storage_public_url.rstrip('/')}/object/sign/"
                f"{cfg.storage_bucket}/{caminho}?token=local-offline&exp={expira_segundos}"
            )
        except ArmazenamentoError:
            if get_settings().app_env != "local":
                raise
            cfg = get_settings()
            url = (
                f"{cfg.storage_public_url.rstrip('/')}/object/sign/"
                f"{cfg.storage_bucket}/{caminho}?token=local-offline&exp={expira_segundos}"
            )
        return UrlDownloadResponse(url=url, expiraEm=expira)
