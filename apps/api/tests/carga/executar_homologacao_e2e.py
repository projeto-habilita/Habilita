"""Homologação E2E via API em Docker Compose nos 4 perfis canônicos ([QA-05] #83).

Executa fluxos reais ponta a ponta contra a API FastAPI, Firebase Auth Emulator,
Firestore Emulator e Supabase Storage local, gerando evidência estruturada em JSON.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

UTC = datetime.UTC
HOSTS_LOCAIS = frozenset({"127.0.0.1", "localhost"})
CHAVE_EMULADOR = "demo-habilita-key"


@dataclass(frozen=True)
class PassoResultado:
    """Registro estruturado de uma verificação executada na homologação E2E."""

    etapa: str
    perfil: str
    metodo: str
    rota: str
    status_http: int
    sucesso: bool
    detalhe: str


def _validar_url_local(url: str, nome: str) -> str:
    parsed = urllib.parse.urlparse(url.strip().rstrip("/"))
    hostname = (parsed.hostname or "").lower()
    if hostname not in HOSTS_LOCAIS:
        msg = (
            f"ABORTADO (ADR 0010 / ADR 0012): {nome} deve apontar exclusivamente para "
            f"127.0.0.1 ou localhost. Recebido: '{url}'."
        )
        raise RuntimeError(msg)
    return url.strip().rstrip("/")


def _gerar_cnpj_ficticio(semente: int) -> str:
    base_num = 50_000_000 + (semente % 40_000_000)
    base = f"{base_num:08d}0001"
    digitos = [int(c) for c in base]
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma1 = sum(digitos[i] * pesos1[i] for i in range(12))
    r1 = soma1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    digitos.append(d1)
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma2 = sum(digitos[i] * pesos2[i] for i in range(13))
    r2 = soma2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    digitos.append(d2)
    return "".join(str(d) for d in digitos)


def _gerar_cpf_ficticio(semente: int) -> str:
    base_num = 400_000_000 + (semente % 499_999_999)
    digitos = [int(c) for c in f"{base_num:09d}"]
    if len(set(digitos)) == 1:
        digitos[-1] = (digitos[-1] + 1) % 10
    soma1 = sum(digitos[i] * (10 - i) for i in range(9))
    r1 = soma1 % 11
    digitos.append(0 if r1 < 2 else 11 - r1)
    soma2 = sum(digitos[i] * (11 - i) for i in range(10))
    r2 = soma2 % 11
    digitos.append(0 if r2 < 2 else 11 - r2)
    return "".join(str(d) for d in digitos)


def _pdf_ficticio(rotulo: str) -> bytes:
    return (
        f"%PDF-1.4\n% DOCUMENTO FICTÍCIO — TESTE AUTOMATIZADO HABILITA ({rotulo})\n"
        "1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        "trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    ).encode()


def _autenticar_emulador(
    client: httpx.Client,
    auth_url: str,
    email: str,
    credencial: str,
) -> tuple[str, dict[str, Any]]:
    url = (
        f"{auth_url}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        f"?key={CHAVE_EMULADOR}"
    )
    resp = client.post(
        url,
        json={"email": email, "password": credencial, "returnSecureToken": True},
    )
    if resp.status_code != 200:
        msg = f"Falha ao autenticar '{email}' no Auth Emulator: HTTP {resp.status_code} {resp.text}"
        raise RuntimeError(msg)
    dados: dict[str, Any] = resp.json()
    token = str(dados["idToken"])
    return token, dados


def executar_homologacao() -> dict[str, Any]:
    """Executa a bateria completa de homologação E2E nos 4 perfis canônicos."""
    base_url = _validar_url_local(
        os.environ.get("BASE_URL", "http://127.0.0.1:8000"),
        "BASE_URL",
    )
    auth_url = _validar_url_local(
        os.environ.get("AUTH_EMULATOR_URL", "http://127.0.0.1:9099"),
        "AUTH_EMULATOR_URL",
    )
    credencial_demo = os.environ.get("DEMO_CREDENTIAL", "SenhaDemo123!")
    saida_padrao = Path(tempfile.gettempdir()) / "homologacao-e2e-cp4.json"
    caminho_saida = Path(os.environ.get("HOMOLOGACAO_OUT", str(saida_padrao)))

    semente = int(time.time())
    passos: list[PassoResultado] = []

    def registrar(
        etapa: str,
        perfil: str,
        metodo: str,
        rota: str,
        status_http: int,
        sucesso: bool,
        detalhe: str,
    ) -> None:
        passos.append(
            PassoResultado(
                etapa=etapa,
                perfil=perfil,
                metodo=metodo,
                rota=rota,
                status_http=status_http,
                sucesso=sucesso,
                detalhe=detalhe,
            )
        )
        if not sucesso:
            msg = f"Falha na etapa '{etapa}' ({perfil} {metodo} {rota}): {detalhe}"
            raise AssertionError(msg)

    with httpx.Client(timeout=15.0) as client:
        # -----------------------------------------------------------------
        # 0. Saúde e prontidão
        # -----------------------------------------------------------------
        r_health = client.get(f"{base_url}/healthz")
        registrar(
            "saude",
            "anonimo",
            "GET",
            "/healthz",
            r_health.status_code,
            r_health.status_code == 200 and r_health.json().get("status") == "ok",
            "Liveness OK",
        )
        r_ready = client.get(f"{base_url}/readyz")
        registrar(
            "prontidao",
            "anonimo",
            "GET",
            "/readyz",
            r_ready.status_code,
            r_ready.status_code == 200 and r_ready.json().get("status") == "ok",
            "Readiness (Firestore Emulator) OK",
        )

        # -----------------------------------------------------------------
        # 1. Autenticação real no Firebase Auth Emulator (4 perfis + isolamento)
        # -----------------------------------------------------------------
        contas = {
            "admin_contratante": "admin.alfa@demo.habilita.test",
            "analista_sst": "analista.alfa@demo.habilita.test",
            "terceirizada": "terceirizada1.alfa@demo.habilita.test",
            "porteiro": "porteiro.alfa@demo.habilita.test",
            "terceirizada_outra": "terceirizada2.alfa@demo.habilita.test",
            "admin_beta": "admin.beta@demo.habilita.test",
        }
        tokens: dict[str, str] = {}
        perfis_me: dict[str, dict[str, Any]] = {}

        for chave, email in contas.items():
            tok, _ = _autenticar_emulador(client, auth_url, email, credencial_demo)
            tokens[chave] = tok

        for papel_canonico in ("admin_contratante", "analista_sst", "terceirizada", "porteiro"):
            hdr = {"Authorization": f"Bearer {tokens[papel_canonico]}"}
            r_me = client.get(f"{base_url}/me", headers=hdr)
            corpo_me = r_me.json() if r_me.status_code == 200 else {}
            ok_me = (
                r_me.status_code == 200
                and corpo_me.get("papel") == papel_canonico
                and corpo_me.get("tenantId") == "tnt_alfa"
            )
            perfis_me[papel_canonico] = corpo_me
            registrar(
                "autenticacao_perfil",
                papel_canonico,
                "GET",
                "/me",
                r_me.status_code,
                ok_me,
                f"tenantId={corpo_me.get('tenantId')} papel={corpo_me.get('papel')}",
            )

        hdr_admin = {"Authorization": f"Bearer {tokens['admin_contratante']}"}
        hdr_analista = {"Authorization": f"Bearer {tokens['analista_sst']}"}
        hdr_terc = {"Authorization": f"Bearer {tokens['terceirizada']}"}
        hdr_porteiro = {"Authorization": f"Bearer {tokens['porteiro']}"}
        hdr_terc2 = {"Authorization": f"Bearer {tokens['terceirizada_outra']}"}
        hdr_beta = {"Authorization": f"Bearer {tokens['admin_beta']}"}

        # -----------------------------------------------------------------
        # 2. Jornada J1 (admin_contratante): Terceirizada, TipoDocumento, Contrato, Exigências
        # -----------------------------------------------------------------
        cnpj_novo = _gerar_cnpj_ficticio(semente)
        r_terc = client.post(
            f"{base_url}/terceirizadas",
            headers=hdr_admin,
            json={"razaoSocial": f"Empresa E2E QA Ltda {semente}", "cnpj": cnpj_novo},
        )
        terc_criada_id = r_terc.json().get("id", "") if r_terc.status_code == 201 else ""
        registrar(
            "J1_criar_terceirizada",
            "admin_contratante",
            "POST",
            "/terceirizadas",
            r_terc.status_code,
            r_terc.status_code == 201 and bool(terc_criada_id),
            f"terceirizadaId={terc_criada_id}",
        )

        r_tipo = client.post(
            f"{base_url}/tipos-documento",
            headers=hdr_admin,
            json={"nome": f"Certificado E2E NR-10 {semente}", "validadeMeses": 24},
        )
        tipo_criado_id = r_tipo.json().get("id", "") if r_tipo.status_code == 201 else ""
        registrar(
            "J1_criar_tipo_documento",
            "admin_contratante",
            "POST",
            "/tipos-documento",
            r_tipo.status_code,
            r_tipo.status_code == 201 and bool(tipo_criado_id),
            f"tipoDocumentoId={tipo_criado_id}",
        )

        # Contrato vinculado a ter_alfa_1 para permitir atuação da conta terceirizada1.alfa em J2
        r_ctr = client.post(
            f"{base_url}/contratos",
            headers=hdr_admin,
            json={
                "identificador": f"CTR-E2E-{semente}",
                "terceirizadaId": "ter_alfa_1",
                "dataInicio": "2026-01-01",
                "dataFim": "2027-12-31",
            },
        )
        ctr_id = r_ctr.json().get("id", "") if r_ctr.status_code == 201 else ""
        registrar(
            "J1_criar_contrato",
            "admin_contratante",
            "POST",
            "/contratos",
            r_ctr.status_code,
            r_ctr.status_code == 201 and bool(ctr_id),
            f"contratoId={ctr_id}",
        )

        r_exig = client.put(
            f"{base_url}/contratos/{ctr_id}/exigencias",
            headers=hdr_admin,
            json={"tipoDocumentoIds": ["tdoc_aso", tipo_criado_id]},
        )
        exig_json = r_exig.json() if r_exig.status_code == 200 else {}
        registrar(
            "J1_definir_exigencias",
            "admin_contratante",
            "PUT",
            f"/contratos/{ctr_id}/exigencias",
            r_exig.status_code,
            (
                r_exig.status_code == 200
                and exig_json.get("versaoMatriz") == 1
                and len(exig_json.get("exigencias", [])) == 2
            ),
            f"versaoMatriz={exig_json.get('versaoMatriz')} "
            f"qtd={len(exig_json.get('exigencias', []))}",
        )

        # -----------------------------------------------------------------
        # 3. Jornada J2 (terceirizada): Trabalhador, Alocação, Upload PDF + Signed URL
        # -----------------------------------------------------------------
        cpf_novo = _gerar_cpf_ficticio(semente)
        r_trb = client.post(
            f"{base_url}/trabalhadores",
            headers=hdr_terc,
            json={"nome": f"Trabalhador Homologacao {semente}", "cpf": cpf_novo},
        )
        trb_json = r_trb.json() if r_trb.status_code == 201 else {}
        trb_id = str(trb_json.get("id", ""))
        registrar(
            "J2_criar_trabalhador",
            "terceirizada",
            "POST",
            "/trabalhadores",
            r_trb.status_code,
            (
                r_trb.status_code == 201
                and bool(trb_id)
                and trb_json.get("terceirizadaId") == "ter_alfa_1"
                and str(trb_json.get("cpfMascarado", "")).startswith("***.")
                and cpf_novo not in r_trb.text
            ),
            f"trabalhadorId={trb_id} cpfMascarado={trb_json.get('cpfMascarado')}",
        )

        r_aloc = client.post(
            f"{base_url}/contratos/{ctr_id}/alocacoes",
            headers=hdr_terc,
            json={"trabalhadorId": trb_id},
        )
        aloc_json = r_aloc.json() if r_aloc.status_code == 201 else {}
        registrar(
            "J2_vincular_alocacao",
            "terceirizada",
            "POST",
            f"/contratos/{ctr_id}/alocacoes",
            r_aloc.status_code,
            (
                r_aloc.status_code == 201
                and aloc_json.get("semaforo") == "VERMELHO"
                and len(aloc_json.get("pendencias", [])) == 2
            ),
            f"semaforo_inicial={aloc_json.get('semaforo')} "
            f"pendencias={len(aloc_json.get('pendencias', []))}",
        )

        hoje = datetime.datetime.now(UTC).date()
        valido_ate_ok = (hoje + datetime.timedelta(days=200)).isoformat()
        emissao_ok = (hoje - datetime.timedelta(days=5)).isoformat()

        pdf_aso = _pdf_ficticio(f"ASO-{semente}")
        sha_aso = hashlib.sha256(pdf_aso).hexdigest()
        r_up_aso = client.post(
            f"{base_url}/trabalhadores/{trb_id}/documentos",
            headers=hdr_terc,
            files={"arquivo": ("aso_ficticio.pdf", pdf_aso, "application/pdf")},
            data={
                "tipoDocumentoId": "tdoc_aso",
                "validoAte": valido_ate_ok,
                "dataEmissao": emissao_ok,
            },
        )
        doc_aso_json = r_up_aso.json() if r_up_aso.status_code == 201 else {}
        doc_aso_id = str(doc_aso_json.get("id", ""))
        registrar(
            "J2_upload_pdf_aso",
            "terceirizada",
            "POST",
            f"/trabalhadores/{trb_id}/documentos",
            r_up_aso.status_code,
            (
                r_up_aso.status_code == 201
                and bool(doc_aso_id)
                and doc_aso_json.get("statusRevisao") == "PENDENTE"
                and doc_aso_json.get("arquivo", {}).get("sha256") == sha_aso
            ),
            f"documentoId={doc_aso_id} status={doc_aso_json.get('statusRevisao')}",
        )

        # Download via signed URL gerada no Supabase Storage local
        r_signed = client.post(
            f"{base_url}/documentos/{doc_aso_id}/url-download",
            headers=hdr_terc,
        )
        signed_json = r_signed.json() if r_signed.status_code == 200 else {}
        signed_url = str(signed_json.get("url", ""))
        registrar(
            "J2_gerar_url_download",
            "terceirizada",
            "POST",
            f"/documentos/{doc_aso_id}/url-download",
            r_signed.status_code,
            r_signed.status_code == 200 and "token=" in signed_url,
            "URL assinada gerada",
        )

        r_download = client.get(signed_url)
        registrar(
            "J2_download_storage_assinado",
            "anonimo_signed_url",
            "GET",
            "/storage/signed-url",
            r_download.status_code,
            r_download.status_code == 200 and r_download.content == pdf_aso,
            f"bytes={len(r_download.content)} sha256_match={r_download.content == pdf_aso}",
        )

        # Upload do segundo tipo exigido (para aprovação) e terceiro arquivo (para rejeição)
        pdf_tipo = _pdf_ficticio(f"NR10-{semente}")
        r_up_tipo = client.post(
            f"{base_url}/trabalhadores/{trb_id}/documentos",
            headers=hdr_terc,
            files={"arquivo": ("nr10_ficticio.pdf", pdf_tipo, "application/pdf")},
            data={
                "tipoDocumentoId": tipo_criado_id,
                "validoAte": valido_ate_ok,
                "dataEmissao": emissao_ok,
            },
        )
        doc_tipo_id = str(r_up_tipo.json().get("id", "")) if r_up_tipo.status_code == 201 else ""
        registrar(
            "J2_upload_pdf_nr10",
            "terceirizada",
            "POST",
            f"/trabalhadores/{trb_id}/documentos",
            r_up_tipo.status_code,
            r_up_tipo.status_code == 201 and bool(doc_tipo_id),
            f"documentoId={doc_tipo_id}",
        )

        pdf_rej = _pdf_ficticio(f"REJ-{semente}")
        r_up_rej = client.post(
            f"{base_url}/trabalhadores/{trb_id}/documentos",
            headers=hdr_terc,
            files={"arquivo": ("nr10_ilegivel.pdf", pdf_rej, "application/pdf")},
            data={
                "tipoDocumentoId": tipo_criado_id,
                "validoAte": valido_ate_ok,
                "dataEmissao": emissao_ok,
            },
        )
        doc_rej_id = str(r_up_rej.json().get("id", "")) if r_up_rej.status_code == 201 else ""
        registrar(
            "J2_upload_pdf_para_rejeicao",
            "terceirizada",
            "POST",
            f"/trabalhadores/{trb_id}/documentos",
            r_up_rej.status_code,
            r_up_rej.status_code == 201 and bool(doc_rej_id),
            f"documentoId={doc_rej_id}",
        )

        # -----------------------------------------------------------------
        # 4. Jornada J3 (analista_sst): Fila SST, Aprovação, Rejeição e Recálculo
        # -----------------------------------------------------------------
        docs_fila_ids: set[str] = set()
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"statusRevisao": "PENDENTE", "limite": 50}
            if cursor:
                params["cursor"] = cursor
            r_fila = client.get(f"{base_url}/documentos", headers=hdr_analista, params=params)
            if r_fila.status_code != 200:
                registrar(
                    "J3_listar_fila_sst",
                    "analista_sst",
                    "GET",
                    "/documentos?statusRevisao=PENDENTE",
                    r_fila.status_code,
                    False,
                    r_fila.text,
                )
            fila_json = r_fila.json()
            for item in fila_json.get("itens", []):
                docs_fila_ids.add(str(item["id"]))
            cursor = fila_json.get("proximoCursor")
            if not cursor:
                break

        registrar(
            "J3_listar_fila_sst",
            "analista_sst",
            "GET",
            "/documentos?statusRevisao=PENDENTE",
            200,
            {doc_aso_id, doc_tipo_id, doc_rej_id}.issubset(docs_fila_ids),
            f"total_pendentes={len(docs_fila_ids)} documentos_upload_encontrados=3",
        )

        # Rejeição com motivo curto (< 10 chars) deve falhar (400 RFC 9457)
        r_rej_curto = client.post(
            f"{base_url}/documentos/{doc_rej_id}/rejeicao",
            headers=hdr_analista,
            json={"motivo": "   curto  "},
        )
        registrar(
            "J3_rejeicao_motivo_curto_bloqueada",
            "analista_sst",
            "POST",
            f"/documentos/{doc_rej_id}/rejeicao",
            r_rej_curto.status_code,
            (
                r_rej_curto.status_code == 400
                and "application/problem+json" in r_rej_curto.headers.get("content-type", "")
            ),
            "Motivo < 10 chars bloqueado via validação Pydantic/RFC 9457 (HTTP 400)",
        )

        # Rejeição com motivo válido (>= 10 chars)
        motivo_valido = "Carimbo do responsavel tecnico ilegivel na digitalizacao."
        r_rej = client.post(
            f"{base_url}/documentos/{doc_rej_id}/rejeicao",
            headers=hdr_analista,
            json={"motivo": motivo_valido},
        )
        rej_json = r_rej.json() if r_rej.status_code == 200 else {}
        registrar(
            "J3_rejeitar_documento",
            "analista_sst",
            "POST",
            f"/documentos/{doc_rej_id}/rejeicao",
            r_rej.status_code,
            (
                r_rej.status_code == 200
                and rej_json.get("statusRevisao") == "REJEITADO"
                and rej_json.get("motivoRejeicao") == motivo_valido
            ),
            f"status={rej_json.get('statusRevisao')}",
        )

        # Tentativa de aprovar documento já rejeitado deve retornar 409
        r_conflito = client.post(
            f"{base_url}/documentos/{doc_rej_id}/aprovacao",
            headers=hdr_analista,
            json={"validoAte": valido_ate_ok},
        )
        registrar(
            "J3_conflito_transicao_nao_pendente",
            "analista_sst",
            "POST",
            f"/documentos/{doc_rej_id}/aprovacao",
            r_conflito.status_code,
            r_conflito.status_code == 409,
            "Transição de REJEITADO -> APROVADO bloqueada com HTTP 409",
        )

        # Aprovação dos 2 documentos obrigatórios (ASO e novo tipo)
        r_aprov_aso = client.post(
            f"{base_url}/documentos/{doc_aso_id}/aprovacao",
            headers=hdr_analista,
            json={"validoAte": valido_ate_ok},
        )
        registrar(
            "J3_aprovar_aso",
            "analista_sst",
            "POST",
            f"/documentos/{doc_aso_id}/aprovacao",
            r_aprov_aso.status_code,
            (
                r_aprov_aso.status_code == 200
                and r_aprov_aso.json().get("statusRevisao") == "APROVADO"
            ),
            f"status={r_aprov_aso.json().get('statusRevisao')}",
        )

        r_aprov_tipo = client.post(
            f"{base_url}/documentos/{doc_tipo_id}/aprovacao",
            headers=hdr_analista,
            json={"validoAte": valido_ate_ok},
        )
        registrar(
            "J3_aprovar_nr10",
            "analista_sst",
            "POST",
            f"/documentos/{doc_tipo_id}/aprovacao",
            r_aprov_tipo.status_code,
            (
                r_aprov_tipo.status_code == 200
                and r_aprov_tipo.json().get("statusRevisao") == "APROVADO"
            ),
            f"status={r_aprov_tipo.json().get('statusRevisao')}",
        )

        # Recálculo do status do trabalhador (semáforo deve passar de VERMELHO para VERDE)
        r_trb_pos = client.get(f"{base_url}/trabalhadores/{trb_id}", headers=hdr_analista)
        trb_pos_json = r_trb_pos.json() if r_trb_pos.status_code == 200 else {}
        alocacoes_pos = trb_pos_json.get("alocacoes", [])
        aloc_alvo: dict[str, Any] = next(
            (a for a in alocacoes_pos if a.get("contratoId") == ctr_id),
            {},
        )
        registrar(
            "J3_recalculo_semaforo_trabalhador",
            "analista_sst",
            "GET",
            f"/trabalhadores/{trb_id}",
            r_trb_pos.status_code,
            (
                r_trb_pos.status_code == 200
                and aloc_alvo.get("semaforo") == "VERDE"
                and aloc_alvo.get("pendencias") == []
            ),
            f"semaforo_final={aloc_alvo.get('semaforo')} pendencias={aloc_alvo.get('pendencias')}",
        )

        # -----------------------------------------------------------------
        # 5. RBAC e Perfil Porteiro
        # -----------------------------------------------------------------
        for rota_bloqueada in ("/terceirizadas", "/contratos", "/trabalhadores", "/documentos"):
            r_port_rbac = client.get(f"{base_url}{rota_bloqueada}", headers=hdr_porteiro)
            registrar(
                f"rbac_porteiro_{rota_bloqueada.strip('/')}",
                "porteiro",
                "GET",
                rota_bloqueada,
                r_port_rbac.status_code,
                r_port_rbac.status_code == 403,
                "Princípio do menor privilégio aplicado (403 Forbidden)",
            )

        r_terc_rbac = client.post(
            f"{base_url}/documentos/{doc_aso_id}/aprovacao",
            headers=hdr_terc,
            json={},
        )
        registrar(
            "rbac_terceirizada_nao_aprova",
            "terceirizada",
            "POST",
            f"/documentos/{doc_aso_id}/aprovacao",
            r_terc_rbac.status_code,
            r_terc_rbac.status_code == 403,
            "Terceirizada bloqueada de aprovar documento (403 Forbidden)",
        )

        r_j5_probe = client.post(
            f"{base_url}/liberacoes/consultas",
            headers=hdr_porteiro,
            json={"cpf": cpf_novo},
        )
        j5_status = r_j5_probe.status_code
        registrar(
            "sonda_j5_portaria",
            "porteiro",
            "POST",
            "/liberacoes/consultas",
            j5_status,
            j5_status in (200, 404),
            (
                "Rota J5 ainda não implementada em b8fef43 (HTTP 404 esperado; escopo pendente)"
                if j5_status == 404
                else "Rota J5 respondeu HTTP 200"
            ),
        )

        # -----------------------------------------------------------------
        # 6. Isolamento Cross-Tenant (tnt_beta -> tnt_alfa) e Cross-Terceirizada
        # -----------------------------------------------------------------
        for rota_iso in (
            f"/contratos/{ctr_id}",
            f"/trabalhadores/{trb_id}",
            f"/documentos/{doc_aso_id}",
        ):
            r_iso_tenant = client.get(f"{base_url}{rota_iso}", headers=hdr_beta)
            registrar(
                f"isolamento_cross_tenant_{rota_iso.split('/')[1]}",
                "admin_beta",
                "GET",
                rota_iso,
                r_iso_tenant.status_code,
                r_iso_tenant.status_code == 404,
                "Tenant Beta não enxerga recurso do Tenant Alfa (404)",
            )

            r_iso_terc = client.get(f"{base_url}{rota_iso}", headers=hdr_terc2)
            registrar(
                f"isolamento_cross_terceirizada_{rota_iso.split('/')[1]}",
                "terceirizada_2_alfa",
                "GET",
                rota_iso,
                r_iso_terc.status_code,
                r_iso_terc.status_code == 404,
                "Terceirizada 2 não enxerga recurso da Terceirizada 1 (404)",
            )

        r_iso_dl_tenant = client.post(
            f"{base_url}/documentos/{doc_aso_id}/url-download",
            headers=hdr_beta,
        )
        registrar(
            "isolamento_cross_tenant_url_download",
            "admin_beta",
            "POST",
            f"/documentos/{doc_aso_id}/url-download",
            r_iso_dl_tenant.status_code,
            r_iso_dl_tenant.status_code == 404,
            "Tenant Beta bloqueado de gerar signed URL de documento do Tenant Alfa (404)",
        )

        r_iso_dl_terc = client.post(
            f"{base_url}/documentos/{doc_aso_id}/url-download",
            headers=hdr_terc2,
        )
        registrar(
            "isolamento_cross_terceirizada_url_download",
            "terceirizada_2_alfa",
            "POST",
            f"/documentos/{doc_aso_id}/url-download",
            r_iso_dl_terc.status_code,
            r_iso_dl_terc.status_code == 404,
            "Terceirizada 2 bloqueada de gerar signed URL de outra terceirizada (404)",
        )

    evidencia: dict[str, Any] = {
        "referencia": "[QA-05] #83",
        "executadoEm": datetime.datetime.now(UTC).isoformat(),
        "alvo": "docker-compose-local",
        "baseUrl": base_url,
        "authEmulatorUrl": auth_url,
        "perfisAutenticados": {
            papel: {
                "uid": info.get("uid"),
                "tenantId": info.get("tenantId"),
                "papel": info.get("papel"),
                "terceirizadaId": info.get("terceirizadaId"),
            }
            for papel, info in perfis_me.items()
        },
        "recursosCriados": {
            "terceirizadaId": terc_criada_id,
            "tipoDocumentoId": tipo_criado_id,
            "contratoId": ctr_id,
            "trabalhadorId": trb_id,
            "documentoAsoId": doc_aso_id,
            "documentoTipoId": doc_tipo_id,
            "documentoRejeitadoId": doc_rej_id,
        },
        "resumo": {
            "totalVerificacoes": len(passos),
            "aprovadas": sum(1 for p in passos if p.sucesso),
            "falhas": sum(1 for p in passos if not p.sucesso),
            "j5PortariaImplementada": j5_status == 200,
        },
        "passos": [asdict(p) for p in passos],
    }

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    caminho_saida.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False), encoding="utf-8")
    return evidencia


def main() -> int:
    """Ponto de entrada CLI para a homologação E2E via API."""
    try:
        evidencia = executar_homologacao()
    except Exception as exc:
        print(f"ERRO NA HOMOLOGACAO E2E: {exc}", file=sys.stderr)
        return 1

    resumo = evidencia["resumo"]
    print(
        f"HOMOLOGACAO E2E CONCLUIDA: {resumo['aprovadas']}/{resumo['totalVerificacoes']} "
        f"verificacoes aprovadas (j5PortariaImplementada={resumo['j5PortariaImplementada']})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
