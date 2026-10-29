"""Testes de isolamento multi-tenant (História H19 / Requisito RNF01).

Especificação:
- História: H19 (Isolar dados por cliente e papel)
- Requisito Não Funcional: RNF01 (Autorização por cliente e papel em 100% das rotas)
- ADR Relacionada: ADR 0002 (Multi-tenant e autorização)
- Casos de Teste da Estratégia de QA: T-ISO-01 a T-ISO-10
- Referência: docs/testes/estrategia.md (Seção 5) e seed/README.md (Seção 4)

Nota para o Checkpoint 1 (18/10/2026):
Conforme definido no Guia do PO (Checkpoint 1) e na ADR 0002:
"Teste de isolamento de tenant escrito (nos projetos com várias empresas clientes).
Ele verifica que um usuário da empresa A recebe erro 403 ou 404 ao acessar dados da
empresa B. O teste pode falhar agora, porque o sistema ainda não existe, mas ele
precisa estar escrito."
Endpoints previstos para implementação completa na Semana 4 (01/11/2026).
"""

import os
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import (
    CPF_TRB_BETA_1,
    CTR_ALFA_1,
    CTR_BETA_1,
    DOC_ALFA_1_ASO,
    DOC_BETA_1_ASO,
    TER_ALFA_1,
    TNT_ALFA,
    TNT_BETA,
    TRB_ALFA_1,
    TRB_ALFA_Y1,
    TRB_BETA_1,
)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rotas /contratos/{id}, /trabalhadores/{id} e /documentos/{id} inexistentes (Semana 4). "
        "Atualmente retornam 404 de rota não encontrada do FastAPI em vez do 404 Problem Details "
        "(RFC 9457, application/problem+json) da camada de autorização."
    ),
)
def test_h19_ca1_t_iso_01_gestor_acessa_recursos_outro_tenant_retorna_404(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """T-ISO-01 (H19.CA1): Gestor de A acessa recursos do Tenant B por ID.

    Dado:
        Um usuário autenticado como Gestor SST do Tenant Alfa (tnt_alfa).
    Quando:
        O gestor tenta obter recursos pertencentes exclusivamente ao Tenant Beta (tnt_beta):
        - GET /contratos/{id_B}
        - GET /trabalhadores/{id_B}
        - GET /documentos/{id_B}
    Então:
        A API responde HTTP 404 em todos os endpoints, com payload Problem Details (RFC 9457)
        oriundo da camada de autorização/guard de tenant, sem expor que o recurso existe
        nem vazar nenhum dado de negócio do Tenant Beta no corpo da resposta.
    """
    recursos_beta = [
        f"/contratos/{CTR_BETA_1}",
        f"/trabalhadores/{TRB_BETA_1}",
        f"/documentos/{DOC_BETA_1_ASO}",
    ]

    for rota in recursos_beta:
        resposta = client.get(rota, headers=headers_gestor_alfa)

        assert resposta.status_code == 404, (
            f"Esperado HTTP 404 ao acessar recurso de outro tenant em {rota}, "
            f"mas obteve HTTP {resposta.status_code}."
        )

        # Valida que o 404 vem da camada de autorização (formato problem+json da RFC 9457/7807)
        # e não um 404 genérico de rota inexistente do FastAPI
        assert "application/problem+json" in resposta.headers.get("content-type", ""), (
            f"Esperado header Content-Type 'application/problem+json' em {rota}, "
            f"mas obteve '{resposta.headers.get('content-type')}'."
        )
        corpo_json = resposta.json()
        assert corpo_json.get("status") == 404, (
            f"Esperado campo 'status': 404 no corpo problem+json em {rota}, "
            f"mas obteve {corpo_json.get('status')}."
        )
        assert "title" in corpo_json or "type" in corpo_json, (
            f"Corpo da resposta em {rota} não segue o contrato Problem Details: {corpo_json}"
        )

        corpo_texto = resposta.text.lower()
        # Não pode conter identificadores confidenciais do tenant B
        assert TNT_BETA not in corpo_texto, f"Vazamento de tenantId de B em {rota}!"
        assert "beta" not in corpo_texto or "detail" in corpo_texto, (
            f"Vazamento de dados do recurso de B no corpo da resposta em {rota}."
        )


@pytest.mark.xfail(
    strict=True,
    reason="Endpoints de listagem (H01, H02, H19) previstos para implementação na Semana 4.",
)
def test_h19_ca1_t_iso_02_gestor_lista_recursos_nao_enxerga_outro_tenant(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """T-ISO-02 (H19.CA1): Gestor de A lista recursos e não enxerga nada de B.

    Dado:
        Um usuário autenticado como Gestor SST do Tenant Alfa (tnt_alfa).
    Quando:
        O gestor lista contratos, trabalhadores, documentos, alertas e auditoria.
    Então:
        A API responde HTTP 200 contendo apenas itens pertencentes ao Tenant Alfa.
        Nenhum item pertencente ao Tenant Beta pode ser retornado na listagem.
    """
    rotas_listagem = [
        "/contratos",
        "/trabalhadores",
        "/documentos",
        "/alertas",
        "/auditoria",
    ]

    for rota in rotas_listagem:
        resposta = client.get(rota, headers=headers_gestor_alfa)
        assert resposta.status_code == 200, f"Falha ao listar {rota}: status {resposta.status_code}"

        dados: dict[str, Any] = resposta.json()
        itens: list[dict[str, Any]] = dados.get("itens", [])

        # Garante que nenhum item de Beta está presente
        for item in itens:
            item_str = str(item).lower()
            assert "beta" not in item_str, f"Recurso do Tenant Beta vazou na listagem de {rota}!"
            assert item.get("tenantId", TNT_ALFA) == TNT_ALFA


@pytest.mark.xfail(
    strict=True,
    reason="Rota POST /contratos prevista para implementação na Semana 4 (ADR 0002).",
)
def test_h19_ca5_t_iso_03_injecao_tenant_id_criacao_contrato_isolado_no_tenant_da_claim(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """T-ISO-03 (H19.CA5): Tentativa de injeção de tenantId no payload/query/header.

    Dado:
        Um usuário autenticado do Tenant Alfa (tnt_alfa).
    Quando:
        O usuário tenta criar um contrato enviando tenantId="tnt_beta" no corpo JSON,
        na query string (?tenantId=tnt_beta) e no header (X-Tenant-Id: tnt_beta).
    Então:
        Conforme a ADR 0002 e AGENTS.md, é terminantemente PROIBIDO aceitar tenantId
        da requisição. A API deve rejeitar o campo extra (400/422) ou ignorá-lo e
        persistir o recurso exclusivamente no tenant da claim (tnt_alfa).
        Nenhum dado pode ser criado ou modificado no Tenant Beta.
    """
    payload_com_injecao = {
        "identificador": "CTR-TEST-INJECTION-01",
        "terceirizadaId": TER_ALFA_1,
        "tenantId": TNT_BETA,  # Tentativa de injeção direta no payload
    }

    headers_com_injecao = {
        **headers_gestor_alfa,
        "X-Tenant-Id": TNT_BETA,  # Tentativa de injeção via header
    }

    resposta = client.post(
        "/contratos?tenantId=tnt_beta",  # Tentativa de injeção via query parameter
        json=payload_com_injecao,
        headers=headers_com_injecao,
    )

    # Aceita 400/422 se o schema Pydantic com extra="forbid" rejeitar o tenantId no payload
    # OU 201 se o endpoint criou o recurso ignorando o parâmetro e usando o tenant da claim
    assert resposta.status_code in (201, 400, 422), (
        f"Código inesperado na tentativa de injeção: {resposta.status_code}"
    )

    if resposta.status_code == 201:
        contrato_criado: dict[str, Any] = resposta.json()
        assert contrato_criado.get("tenantId") != TNT_BETA, (
            "VULNERABILIDADE CRÍTICA: Contrato criado no tenant injetado (tnt_beta)!"
        )
        assert contrato_criado.get("tenantId", TNT_ALFA) == TNT_ALFA


@pytest.mark.xfail(
    strict=True,
    reason="Rota POST /liberacoes/consultas prevista para implementação na Semana 4 (ADR 0008).",
)
def test_h19_ca1_t_iso_04_portaria_consulta_cpf_outro_tenant_retorna_nao_cadastrado(
    client: TestClient,
    headers_porteiro_alfa: dict[str, str],
) -> None:
    """T-ISO-04 (H19.CA1): Portaria de A consulta CPF que só existe no Tenant B.

    Dado:
        Um porteiro do Tenant Alfa consultando liberação de acesso na portaria.
    Quando:
        Consulta via POST /liberacoes/consultas um CPF que está cadastrado apenas
        no Tenant Beta (trb_beta_1).
    Então:
        A consulta responde 200 com resultado="NAO_CADASTRADO".
        Nenhum dado pessoal do trabalhador do Tenant Beta (nome, empresa, contratos)
        pode ser revelado ao porteiro do Tenant Alfa.
    """
    payload_consulta = {
        "cpf": CPF_TRB_BETA_1,  # CPF pertencente a Alexandre Pires Barbosa (tnt_beta)
    }

    resposta = client.post(
        "/liberacoes/consultas",
        json=payload_consulta,
        headers=headers_porteiro_alfa,
    )

    assert resposta.status_code == 200, (
        f"Esperado HTTP 200 na consulta da portaria, obtido {resposta.status_code}"
    )

    resultado: dict[str, Any] = resposta.json()
    assert resultado.get("resultado") == "NAO_CADASTRADO", (
        f"Esperado 'NAO_CADASTRADO', mas obteve '{resultado.get('resultado')}'"
    )

    # Valida que não houve vazamento de dados do trabalhador de B
    assert "trabalhador" not in resultado or resultado.get("trabalhador") is None
    assert resultado.get("contratosAptos") == []
    assert "Alexandre Pires Barbosa" not in resposta.text


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rota GET /trabalhadores/{id} inexistente (Semana 4). "
        "Atualmente retorna 404 de rota não encontrada do FastAPI em vez do 404 Problem Details "
        "(RFC 9457, application/problem+json) da camada de autorização."
    ),
)
def test_h19_ca3_t_iso_05_terceirizada_acessa_trabalhador_outra_terceirizada_retorna_404(
    client: TestClient,
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """T-ISO-05 (H19.CA3): Terceirizada 1 de A tenta acessar trabalhador da Terceirizada 2.

    Dado:
        Usuário com papel 'terceirizada' vinculado à Terceirizada 1 (ter_alfa_1).
    Quando:
        Faz GET em /trabalhadores/{trb_alfa_y1}, que pertence à Terceirizada 2 (ter_alfa_2).
    Então:
        A API responde HTTP 404 (isolamento horizontal intra-tenant entre terceirizadas),
        com formato Problem Details (RFC 9457, application/problem+json).
    """
    rota = f"/trabalhadores/{TRB_ALFA_Y1}"
    resposta = client.get(rota, headers=headers_terceirizada_1_alfa)

    assert resposta.status_code == 404, (
        f"Esperado HTTP 404 no acesso entre terceirizadas distintas em {rota}, "
        f"mas obteve HTTP {resposta.status_code}."
    )
    assert "application/problem+json" in resposta.headers.get("content-type", ""), (
        f"Esperado header Content-Type 'application/problem+json' em {rota}, "
        f"mas obteve '{resposta.headers.get('content-type')}'."
    )
    corpo_json = resposta.json()
    assert corpo_json.get("status") == 404, (
        f"Esperado campo 'status': 404 no corpo problem+json em {rota}, "
        f"mas obteve {corpo_json.get('status')}."
    )
    assert "title" in corpo_json or "type" in corpo_json, (
        f"Corpo da resposta em {rota} não segue o contrato Problem Details: {corpo_json}"
    )


def test_h19_ca4_t_iso_06_acesso_direto_firestore_storage_sem_api_rejeitado_deny_all() -> None:
    """T-ISO-06 (H19.CA4): Regras deny-all no emulador bloqueiam acesso ao Firestore e Storage.

    Dado:
        O navegador ou cliente externo tentando ler ou escrever diretamente
        no Firestore ou Cloud Storage sem passar pela API FastAPI.
    Quando:
        Operações de leitura (GET) e escrita (POST) não autenticadas são executadas
        diretamente contra os emuladores locais.
    Então:
        1. Firestore rejeita leitura não autenticada com HTTP 403 (PERMISSION_DENIED).
        2. Firestore rejeita escrita não autenticada com HTTP 403 (PERMISSION_DENIED).
        3. Cloud Storage rejeita leitura não autenticada com HTTP 403.
        4. Cloud Storage rejeita escrita não autenticada com HTTP 403.
        Comprova a diretiva deny-all em tempo de execução sem depender de busca de texto.
    """
    firestore_host = os.getenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    firestore_base = (
        firestore_host.rstrip("/") if "://" in firestore_host else f"http://{firestore_host}"
    )

    storage_host = (
        os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
        or os.getenv("STORAGE_EMULATOR_HOST")
        or "127.0.0.1:9199"
    )
    storage_base = storage_host.rstrip("/") if "://" in storage_host else f"http://{storage_host}"

    project_id = os.getenv("FIREBASE_PROJECT_ID", "demo-habilita")
    storage_bucket = os.getenv("STORAGE_BUCKET_NAME", f"{project_id}.appspot.com")

    probe_doc = "probe_t_iso_06"
    url_fs_docs = (
        f"{firestore_base}/v1/projects/{project_id}/databases/(default)/documents/{probe_doc}"
    )
    url_st_file = f"{storage_base}/v0/b/{storage_bucket}/o/{probe_doc}.txt"
    url_st_upload = f"{storage_base}/v0/b/{storage_bucket}/o?name={probe_doc}.txt"

    try:
        with httpx.Client(timeout=5.0) as http_client:
            # 1. Firestore: leitura não autenticada negada
            resp_fs_read = http_client.get(url_fs_docs)
            assert resp_fs_read.status_code == 403, (
                f"Esperado HTTP 403 na leitura do Firestore, obtido {resp_fs_read.status_code}"
            )
            fs_read_json = resp_fs_read.json()
            assert fs_read_json.get("error", {}).get("status") == "PERMISSION_DENIED", (
                f"Esperado PERMISSION_DENIED no Firestore, obtido: {fs_read_json}"
            )

            # 2. Firestore: escrita não autenticada negada
            resp_fs_write = http_client.post(
                url_fs_docs,
                json={"fields": {"probe": {"stringValue": "t_iso_06_unauthenticated"}}},
            )
            assert resp_fs_write.status_code == 403, (
                f"Esperado HTTP 403 na escrita do Firestore, obtido {resp_fs_write.status_code}"
            )
            fs_write_json = resp_fs_write.json()
            assert fs_write_json.get("error", {}).get("status") == "PERMISSION_DENIED", (
                f"Esperado PERMISSION_DENIED no Firestore, obtido: {fs_write_json}"
            )

            # 3. Storage: leitura não autenticada negada
            resp_st_read = http_client.get(url_st_file)
            assert resp_st_read.status_code == 403, (
                f"Esperado HTTP 403 na leitura do Storage, obtido {resp_st_read.status_code}"
            )
            st_read_json = resp_st_read.json()
            assert "permission" in str(st_read_json).lower(), (
                f"Resposta do Storage não indica negação de permissão: {st_read_json}"
            )

            # 4. Storage: escrita não autenticada negada
            resp_st_write = http_client.post(
                url_st_upload,
                content=b"dados_nao_autenticados_t_iso_06",
                headers={"Content-Type": "text/plain"},
            )
            assert resp_st_write.status_code == 403, (
                f"Esperado HTTP 403 na escrita do Storage, obtido {resp_st_write.status_code}"
            )
            st_write_json = resp_st_write.json()
            assert "permission" in str(st_write_json).lower(), (
                f"Resposta do Storage não indica negação de permissão: {st_write_json}"
            )
    except httpx.ConnectError:
        pytest.skip("Emuladores locais do Firebase não acessíveis para executar T-ISO-06.")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rota POST /documentos/{id}/url-download inexistente (Semana 4). "
        "Atualmente retorna 404 de rota não encontrada do FastAPI em vez do 404 Problem Details "
        "(RFC 9457, application/problem+json) da camada de autorização."
    ),
)
def test_h19_ca1_t_iso_07_gestor_gera_url_download_documento_outro_tenant_retorna_404(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """T-ISO-07 (H19.CA1): Gestor de A tenta gerar URL assinada para documento de B.

    Dado:
        Um usuário Gestor SST do Tenant Alfa.
    Quando:
        Tenta gerar link temporário de download (POST /documentos/{id_B}/url-download)
        para um documento armazenado sob o escopo do Tenant Beta (doc_beta_1_aso).
    Então:
        A API responde HTTP 404, bloqueando a geração de URL assinada para outro tenant,
        com formato Problem Details (RFC 9457, application/problem+json).
    """
    rota = f"/documentos/{DOC_BETA_1_ASO}/url-download"
    resposta = client.post(rota, headers=headers_gestor_alfa)

    assert resposta.status_code == 404, (
        f"Esperado HTTP 404 ao solicitar URL de documento de outro tenant em {rota}, "
        f"mas obteve HTTP {resposta.status_code}."
    )
    assert "application/problem+json" in resposta.headers.get("content-type", ""), (
        f"Esperado header Content-Type 'application/problem+json' em {rota}, "
        f"mas obteve '{resposta.headers.get('content-type')}'."
    )
    corpo_json = resposta.json()
    assert corpo_json.get("status") == 404, (
        f"Esperado campo 'status': 404 no corpo problem+json em {rota}, "
        f"mas obteve {corpo_json.get('status')}."
    )
    assert "title" in corpo_json or "type" in corpo_json, (
        f"Corpo da resposta em {rota} não segue o contrato Problem Details: {corpo_json}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rotas protegidas e middleware de autenticação previstos para Semana 4. "
        "Atualmente rota inexistente retorna 404 de rota não encontrada em vez de "
        "401 Problem Details da camada de autenticação."
    ),
)
def test_h19_ca6_t_iso_08_requisicao_sem_token_retorna_401_problem_details(
    client: TestClient,
) -> None:
    """T-ISO-08 (H19.CA6): Requisição a rotas protegidas sem token retorna 401 Problem Details.

    Dado:
        Um cliente não autenticado (sem cabeçalho Authorization nem token).
    Quando:
        Tenta acessar endpoints protegidos de contratos, trabalhadores ou documentos:
        - GET /contratos/{id}
        - GET /trabalhadores/{id}
        - GET /documentos
    Então:
        A API responde HTTP 401 Unauthorized com formato Problem Details (RFC 9457,
        application/problem+json) e status 401 no payload.
    """
    rotas_protegidas = [
        f"/contratos/{CTR_ALFA_1}",
        f"/trabalhadores/{TRB_ALFA_1}",
        "/documentos",
    ]

    for rota in rotas_protegidas:
        resposta = client.get(rota)  # Requisição sem cabeçalho Authorization

        assert resposta.status_code == 401, (
            f"Esperado HTTP 401 para requisição sem token em {rota}, "
            f"mas obteve HTTP {resposta.status_code}."
        )
        assert "application/problem+json" in resposta.headers.get("content-type", ""), (
            f"Esperado Content-Type 'application/problem+json' em {rota}, "
            f"mas obteve '{resposta.headers.get('content-type')}'."
        )
        corpo = resposta.json()
        assert corpo.get("status") == 401, (
            f"Esperado status 401 em {rota}, mas obteve {corpo.get('status')}."
        )
        assert "title" in corpo or "type" in corpo, (
            f"Corpo da resposta em {rota} não segue o padrão Problem Details: {corpo}"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Controle de acesso baseado em papel (RBAC) previsto para Semana 4. "
        "Atualmente rota inexistente retorna 404 de rota não encontrada em vez de "
        "403 Problem Details da camada de autorização."
    ),
)
def test_h19_ca6_t_iso_09_papel_inadequado_retorna_403_problem_details(
    client: TestClient,
    headers_porteiro_alfa: dict[str, str],
    headers_terceirizada_1_alfa: dict[str, str],
) -> None:
    """T-ISO-09 (H19.CA6): Requisições de usuários com papel não autorizado retornam 403.

    Dado:
        Usuários autenticados com papéis específicos ('porteiro' ou 'terceirizada').
    Quando:
        - Porteiro tenta criar contrato (POST /contratos) ou aprovar documento.
        - Terceirizada tenta aprovar documento de SST (POST /documentos/{id}/aprovacao).
    Então:
        A API responde HTTP 403 Forbidden com formato Problem Details (RFC 9457,
        application/problem+json), garantindo que violações de RBAC sejam bloqueadas.
    """
    # 1. Porteiro tenta criar contrato (ação restrita a Gestor SST)
    payload_contrato = {
        "identificador": "CTR-ILLEGAL-01",
        "terceirizadaId": TER_ALFA_1,
    }
    resp_porteiro = client.post(
        "/contratos",
        json=payload_contrato,
        headers=headers_porteiro_alfa,
    )
    assert resp_porteiro.status_code == 403, (
        f"Esperado HTTP 403 para porteiro criando contrato, mas obteve {resp_porteiro.status_code}."
    )
    assert "application/problem+json" in resp_porteiro.headers.get("content-type", ""), (
        f"Esperado application/problem+json em POST /contratos, "
        f"mas obteve '{resp_porteiro.headers.get('content-type')}'."
    )
    assert resp_porteiro.json().get("status") == 403

    # 2. Terceirizada tenta aprovar documento (ação restrita a Gestor SST)
    resp_terceirizada = client.post(
        f"/documentos/{DOC_ALFA_1_ASO}/aprovacao",
        headers=headers_terceirizada_1_alfa,
    )
    assert resp_terceirizada.status_code == 403, (
        f"Esperado HTTP 403 para terceirizada aprovando documento, "
        f"mas obteve {resp_terceirizada.status_code}."
    )
    assert "application/problem+json" in resp_terceirizada.headers.get("content-type", ""), (
        f"Esperado application/problem+json em aprovação de documento, "
        f"mas obteve '{resp_terceirizada.headers.get('content-type')}'."
    )
    assert resp_terceirizada.json().get("status") == 403


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Rotas e repositórios delimitados por caminho previstos para Semana 4. "
        "Atualmente rota inexistente retorna 404 de rota não encontrada em vez do "
        "404 Problem Details da camada de autorização."
    ),
)
def test_h19_ca1_t_iso_10_ids_parecidos_ou_colidentes_entre_tenants_retorna_404(
    client: TestClient,
    headers_gestor_alfa: dict[str, str],
) -> None:
    """T-ISO-10 (H19.CA1): IDs parecidos, colidentes ou com prefixos similares retornam 404.

    Dado:
        Um usuário autenticado como Gestor SST do Tenant Alfa (tnt_alfa).
    Quando:
        O gestor tenta acessar recursos cujos IDs se assemelham aos de Alfa,
        colidem em nome simples ou tentam path traversal:
        - GET /contratos/{id_beta_parecido} (ex.: 'ctr_beta_1' vs 'ctr_alfa_1')
        - GET /contratos/{id_com_sufixo_ou_prefixo}
        - GET /contratos/{tentativa_path_traversal}
    Então:
        A busca delimitada sob 'tenants/tnt_alfa/...' responde HTTP 404 com formato
        Problem Details (RFC 9457), garantindo que correspondências parciais,
        colisões ou prefixos parecidos nunca vazem dados entre tenants.
    """
    ids_fronteira = [
        CTR_BETA_1,
        f"{CTR_ALFA_1}_beta",
        f"beta_{CTR_ALFA_1}",
        f"../tnt_beta/contratos/{CTR_BETA_1}",
    ]

    for recurso_id in ids_fronteira:
        rota = f"/contratos/{recurso_id}"
        resposta = client.get(rota, headers=headers_gestor_alfa)

        assert resposta.status_code == 404, (
            f"Esperado HTTP 404 para ID fronteira/parecido '{recurso_id}' em {rota}, "
            f"mas obteve HTTP {resposta.status_code}."
        )
        assert "application/problem+json" in resposta.headers.get("content-type", ""), (
            f"Esperado Content-Type 'application/problem+json' em {rota}, "
            f"mas obteve '{resposta.headers.get('content-type')}'."
        )
        corpo = resposta.json()
        assert corpo.get("status") == 404
        assert "title" in corpo or "type" in corpo
        assert TNT_BETA not in resposta.text.lower(), f"Vazamento de tenantId de B em {rota}!"
