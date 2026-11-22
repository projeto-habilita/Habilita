"""Testes unitários para persistência dos repositórios no Cloud Firestore (BE-04).

Valida que as operações de criação, leitura, isolamento por tenant e atualização
gravam e consultam nas coleções do Firestore:
- tenants/{tenantId}/terceirizadas/{id}
- tenants/{tenantId}/tiposDocumento/{id}
- tenants/{tenantId}/contratos/{id}
- tenants/{tenantId}/trabalhadores/{id}
- tenants/{tenantId}/indiceCpf/{cpfHash}
- tenants/{tenantId}/documentos/{id}
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from habilita.cadastros.repositorio import CadastrosRepositorio
from habilita.cadastros.schemas import NovaTerceirizada, NovoContrato, NovoTipoDocumento
from habilita.trabalhadores.repositorio import TrabalhadoresRepositorio
from habilita.trabalhadores.schemas import ArquivoMetadados


def test_cadastros_repositorio_grava_no_firestore() -> None:
    """Valida que CadastrosRepositorio grava e lê no Firestore quando db está disponível."""
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_ter_col = MagicMock()
    mock_tip_col = MagicMock()
    mock_ctr_col = MagicMock()

    def get_collection(name: str) -> MagicMock:
        if name == "terceirizadas":
            return mock_ter_col
        if name == "tiposDocumento":
            return mock_tip_col
        if name == "contratos":
            return mock_ctr_col
        return MagicMock()

    mock_tenant_doc.collection.side_effect = get_collection

    repo = CadastrosRepositorio(tenant_id="tnt_teste", db=mock_db)

    # 1. Criação de Terceirizada com CNPJ válido
    nova_ter = NovaTerceirizada(
        razaoSocial="Terceirizada Teste Firestore",
        cnpj="11222333000181",
    )
    ter = repo.criar_terceirizada(nova_ter)
    assert ter.razaoSocial == nova_ter.razaoSocial
    mock_ter_col.document.assert_called_with(ter.id)
    mock_ter_col.document.return_value.set.assert_called_once()

    # 2. Criação de Tipo de Documento
    novo_tipo = NovoTipoDocumento(nome="Certificado Teste", validadeMeses=12)
    tipo = repo.criar_tipo_documento(novo_tipo)
    assert tipo.nome == "Certificado Teste"
    mock_tip_col.document.assert_called_with(tipo.id)
    mock_tip_col.document.return_value.set.assert_called_once()

    # 3. Criação de Contrato
    novo_ctr = NovoContrato(
        identificador="CTR-TESTE-FS-01",
        terceirizadaId=ter.id,
        dataInicio="2026-01-01",
        dataFim="2026-12-31",
    )
    ctr = repo.criar_contrato(novo_ctr)
    assert ctr.identificador == "CTR-TESTE-FS-01"
    mock_ctr_col.document.assert_called_with(ctr.id)
    mock_ctr_col.document.return_value.set.assert_called_once()


def test_trabalhadores_repositorio_grava_no_firestore() -> None:
    """Valida que TrabalhadoresRepositorio grava trabalhador e documento no Firestore."""
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_trb_col = MagicMock()
    mock_indice_col = MagicMock()
    mock_doc_col = MagicMock()

    def get_collection(name: str) -> MagicMock:
        if name == "trabalhadores":
            return mock_trb_col
        if name == "indiceCpf":
            return mock_indice_col
        if name == "documentos":
            return mock_doc_col
        return MagicMock()

    mock_tenant_doc.collection.side_effect = get_collection

    # Simula que o CPF não existe previamente no índice
    mock_indice_doc = MagicMock()
    mock_indice_doc.get.return_value.exists = False
    mock_indice_col.document.return_value = mock_indice_doc

    repo = TrabalhadoresRepositorio(tenant_id="tnt_teste", db=mock_db)

    # 1. Criação de Trabalhador
    trb = repo.criar_trabalhador(
        nome="João da Silva",
        cpf="10100000126",
        terceirizada_id="ter_teste_1",
    )
    assert trb.nome == "João da Silva"
    mock_trb_col.document.assert_called_with(trb.id)
    mock_trb_col.document.return_value.set.assert_called_once()
    mock_indice_doc.set.assert_called_once_with({"trabalhadorId": trb.id})

    # 2. Criação de Documento
    arquivo_meta = ArquivoMetadados(
        contentType="application/pdf",
        tamanhoBytes=2048,
        sha256="a" * 64,
    )
    doc = repo.criar_documento(
        trabalhador_id=trb.id,
        tipo_documento_id="tip_aso",
        valido_ate="2027-01-01",
        data_emissao="2026-01-01",
        arquivo_meta=arquivo_meta,
    )
    assert doc.trabalhadorId == trb.id
    mock_doc_col.document.assert_called_with(doc.id)
    mock_doc_col.document.return_value.set.assert_called_once()

    # 3. Aprovação de Documento
    mock_doc_snap = MagicMock()
    mock_doc_snap.exists = True
    mock_doc_snap.to_dict.return_value = {
        "id": doc.id,
        "trabalhadorId": trb.id,
        "tipoDocumentoId": "tip_aso",
        "validoAte": "2027-01-01",
        "statusRevisao": "PENDENTE",
        "enviadoEm": "2026-10-20T10:00:00Z",
        "dataEmissao": "2026-01-01",
        "revisadoEm": None,
        "motivoRejeicao": None,
        "situacaoValidade": "VALIDO",
        "arquivo": arquivo_meta.model_dump(),
    }
    mock_doc_col.document.return_value.get.return_value = mock_doc_snap

    doc_aprovado = repo.aprovar_documento(doc.id)
    assert doc_aprovado.statusRevisao == "APROVADO"
    mock_doc_col.document.return_value.update.assert_called_once()


def test_trabalhadores_repositorio_compatibilidade_seed_e_isolamento_tenant() -> None:
    """Valida tolerância a campos extras do seed e isolamento por tenant no collection group."""
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_doc_col = MagicMock()
    mock_tenant_doc.collection.return_value = mock_doc_col

    # Documento não existe na coleção direta do tenant
    mock_direct_snap = MagicMock()
    mock_direct_snap.exists = False
    mock_doc_col.document.return_value.get.return_value = mock_direct_snap

    # 1. Simula collection group retornando documento de outro tenant
    doc_outro_tenant = MagicMock()
    doc_outro_tenant.reference.path = (
        "tenants/tnt_outro/trabalhadores/trb_outro/documentos/doc_outro"
    )
    doc_outro_tenant.to_dict.return_value = {
        "id": "doc_outro",
        "trabalhadorId": "trb_outro",
        "tipoDocumentoId": "tdoc_aso",
        "validoAte": "2027-01-01",
        "statusRevisao": "PENDENTE",
        "enviadoEm": "2026-10-20T10:00:00Z",
        "arquivo": {
            "caminho": "tenants/tnt_outro/trabalhadores/trb_outro/documentos/doc_outro/file.pdf",
            "contentType": "application/pdf",
            "tamanhoBytes": 1024,
            "sha256": "b" * 64,
        },
        "revisadoPor": "uid_admin",
        "enviadoPor": "uid_terc",
    }
    cg_stream = mock_db.collection_group.return_value.where.return_value.limit.return_value.stream
    cg_stream.return_value = [doc_outro_tenant]

    repo = TrabalhadoresRepositorio(tenant_id="tnt_meu", db=mock_db)
    # Deve retornar None pois pertence ao tnt_outro
    assert repo.obter_documento("doc_outro") is None

    # 2. Simula collection group retornando documento do mesmo tenant com campos do seed
    doc_meu_tenant = MagicMock()
    doc_meu_tenant.reference.path = "tenants/tnt_meu/trabalhadores/trb_meu/documentos/doc_meu"
    doc_meu_tenant.to_dict.return_value = {
        "id": "doc_meu",
        "trabalhadorId": "trb_meu",
        "tipoDocumentoId": "tdoc_aso",
        "validoAte": "2027-01-01",
        "statusRevisao": "PENDENTE",
        "enviadoEm": "2026-10-20T10:00:00Z",
        "arquivo": {
            "caminho": "tenants/tnt_meu/trabalhadores/trb_meu/documentos/doc_meu/file.pdf",
            "contentType": "application/pdf",
            "tamanhoBytes": 1024,
            "sha256": "c" * 64,
        },
        "revisadoPor": "uid_admin",
        "enviadoPor": "uid_terc",
    }
    cg_stream.return_value = [doc_meu_tenant]

    doc_obtido = repo.obter_documento("doc_meu")
    assert doc_obtido is not None
    assert doc_obtido.id == "doc_meu"
    assert doc_obtido.statusRevisao == "PENDENTE"
    assert doc_obtido.arquivo is not None
    assert doc_obtido.arquivo.sha256 == "c" * 64


def test_bloqueio_fallback_memoria_fora_de_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valida bloqueio estrito de memória fora de ENV=local (BE-01, BE-04)."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CPF_HMAC_KEY", "chave-secreta-de-producao-longa")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-de-teste-prod-nao-demo")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.habilita.com.br")

    # 1. Sem conexão com Firestore, instanciar repositório falha imediatamente
    monkeypatch.setattr("habilita.cadastros.repositorio.obter_firestore_client", lambda: None)
    monkeypatch.setattr("habilita.trabalhadores.repositorio.obter_firestore_client", lambda: None)
    with pytest.raises(RuntimeError, match="Armazenamento em memória proibido fora de ENV=local"):
        CadastrosRepositorio(tenant_id="tnt_alfa")

    with pytest.raises(RuntimeError, match="Armazenamento em memória proibido fora de ENV=local"):
        TrabalhadoresRepositorio(tenant_id="tnt_alfa")

    # 2. Com mock_db conectado, se o documento não existir no Firestore,
    # NÃO deve fazer fallback para o banco em memória (deve retornar None)
    mock_db = MagicMock()
    mock_tenant_doc = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_tenant_doc

    mock_doc_inexistente = MagicMock()
    mock_doc_inexistente.get.return_value.exists = False
    mock_tenant_doc.collection.return_value.document.return_value = mock_doc_inexistente
    cg_mock = mock_db.collection_group.return_value.where.return_value.limit.return_value
    cg_mock.stream.return_value = []

    repo_cad = CadastrosRepositorio(tenant_id="tnt_alfa", db=mock_db)
    # Mesmo "ter_alfa_1" existindo em _BANCO_TERCEIRIZADAS, fora de local retorna None
    assert repo_cad.obter_terceirizada("ter_alfa_1") is None

    repo_trb = TrabalhadoresRepositorio(tenant_id="tnt_alfa", db=mock_db)
    # Mesmo "doc_alfa_1_aso" existindo em _BANCO_DOCUMENTOS, fora de local retorna None
    assert repo_trb.obter_documento("doc_alfa_1_aso") is None


def test_cpf_hmac_key_e_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valida exigência de chave HMAC fora de local e suporte aos aliases APP_ENV e ENV (BE-03)."""
    from pydantic import ValidationError

    from habilita.config import Settings

    # Em local, valor padrão é aceito
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("CPF_HMAC_KEY", raising=False)
    s_local = Settings()
    assert s_local.app_env == "local"

    # Fora de local com APP_ENV, chave padrão é rejeitada
    monkeypatch.setenv("APP_ENV", "ci")
    with pytest.raises(ValidationError, match="CPF_HMAC_KEY deve ser configurada obrigatoriamente"):
        Settings()

    # Fora de local com alias ENV, chave padrão também é rejeitada
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(ValidationError, match="CPF_HMAC_KEY deve ser configurada obrigatoriamente"):
        Settings()

    # Fora de local com chave real e HTTPS, é aceito
    monkeypatch.setenv("CPF_HMAC_KEY", "chave-secreta-producao-longa")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-de-teste-prod-nao-demo")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.habilita.com.br")
    s_prod = Settings()
    assert s_prod.app_env == "prod"
    assert s_prod.cpf_hmac_key == "chave-secreta-producao-longa"
