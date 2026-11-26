# Suíte de Carga (`k6`) e Homologação E2E via API (`[QA-05]`)

Scripts não-`pytest` (sem prefixo `test_`) para validação ponta a ponta da API FastAPI contra Docker Compose local (Firebase Auth Emulator, Firestore Emulator e Supabase Storage local) e teste de carga local com `k6` respeitando as ADRs **0010** e **0012**.

## 1. Subir o ambiente Docker Compose e popular o seed

Na raiz do repositório:

```bash
docker compose up -d --build
```

Aguarde o endpoint de prontidão responder HTTP 200:

```bash
curl -fsS http://127.0.0.1:8000/readyz
```

Execute o seed determinístico:

```bash
docker compose --profile seed run --rm seed
```

## 2. Homologação E2E nos 4 perfis canônicos (`[QA-05] #83`)

Autentica no Firebase Auth Emulator (`127.0.0.1:9099`) como `admin_contratante`, `analista_sst`, `terceirizada` e `porteiro`, valida J1 (cadastros), J2 (vínculo, upload PDF no Supabase Storage local e download por URL assinada), J3 (fila SST, aprovação, rejeição com motivo obrigatório e recálculo do semáforo) e isolamento cross-tenant / cross-terceirizada:

```bash
cd apps/api
uv run python tests/carga/executar_homologacao_e2e.py
```

A evidência estruturada é gravada em `/tmp/homologacao-e2e-cp4.json`.

## 3. Teste de carga local com `k6` (ADR 0010 e ADR 0012)

O script `k6_carga_local.js` exige `__ENV.ALVO` explícito (`"local"` ou `"producao"`):
- **`ALVO=local`**: exige host `127.0.0.1` ou `localhost` e executa **20 VUs concorrentes por 60 s**, com thresholds `p(95) < 800 ms` (`tipo:leitura`), `p(95) < 1500 ms` (`tipo:escrita`) e `http_req_failed < 0.01`.
- **`ALVO=producao`**: impõe os tetos da ADR 0012 (`<= 5` VUs, `<= 5` req/s, máximo `500` requisições totais, somente leitura, aborta em 429/5xx).

Execução local via container oficial `grafana/k6`:

```bash
docker run --rm -i --network host \
  -v "$(pwd)/apps/api/tests/carga:/scripts" \
  -v /tmp:/out \
  -e ALVO=local \
  -e BASE_URL=http://127.0.0.1:8000 \
  grafana/k6 run /scripts/k6_carga_local.js
```

O resumo JSON é exportado via `handleSummary` para `/tmp/k6-resumo-cp4.json`.

## 4. Encerramento do ambiente

```bash
docker compose down -v
```
