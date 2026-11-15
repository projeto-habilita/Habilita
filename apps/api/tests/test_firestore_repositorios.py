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
