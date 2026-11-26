import { check, fail, sleep } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { textSummary } from "https://jslib.k6.io/k6-summary/0.0.4/index.js";

// =============================================================================
// Guardrails obrigatórios (ADR 0010 e ADR 0012)
// =============================================================================
const ALVO = (__ENV.ALVO || "").trim();
if (ALVO !== "local" && ALVO !== "producao") {
  throw new Error(
    `ABORTADO (ADR 0012): __ENV.ALVO é obrigatório e deve ser "local" ou "producao". Recebido: "${ALVO}".`,
  );
}

const BASE_URL = (__ENV.BASE_URL || (ALVO === "local" ? "http://127.0.0.1:8000" : ""))
  .trim()
  .replace(/\/+$/, "");

if (!BASE_URL) {
  throw new Error("ABORTADO: __ENV.BASE_URL não informado.");
}

const HOST_MATCH = BASE_URL.match(/^https?:\/\/([^/:?#]+)/i);
const HOSTNAME = HOST_MATCH ? HOST_MATCH[1].toLowerCase() : "";

if (ALVO === "local") {
  if (HOSTNAME !== "127.0.0.1" && HOSTNAME !== "localhost") {
    throw new Error(
      `ABORTADO (ADR 0010 / ADR 0012): ALVO=local só permite host 127.0.0.1 ou localhost. Recebido: "${BASE_URL}".`,
    );
  }
}

const AUTH_EMULATOR_URL = (__ENV.AUTH_EMULATOR_URL || "http://127.0.0.1:9099")
  .trim()
  .replace(/\/+$/, "");
const AUTH_HOST_MATCH = AUTH_EMULATOR_URL.match(/^https?:\/\/([^/:?#]+)/i);
const AUTH_HOSTNAME = AUTH_HOST_MATCH ? AUTH_HOST_MATCH[1].toLowerCase() : "";

if (ALVO === "local" && AUTH_HOSTNAME !== "127.0.0.1" && AUTH_HOSTNAME !== "localhost") {
  throw new Error(
    `ABORTADO (ADR 0010): AUTH_EMULATOR_URL deve apontar para 127.0.0.1 ou localhost. Recebido: "${AUTH_EMULATOR_URL}".`,
  );
}

// Tetos rígidos de produção (ADR 0012): <= 5 VUs, <= 5 req/s, <= 500 reqs totais
const ADR0012_MAX_VUS = 5;
const ADR0012_MAX_RPS = 5;
const ADR0012_MAX_REQS = 500;

const PROD_VUS = Number(__ENV.PROD_VUS || ADR0012_MAX_VUS);
const PROD_RPS = Number(__ENV.PROD_RPS || ADR0012_MAX_RPS);
const PROD_DURATION_S = Number(__ENV.PROD_DURATION_S || 60);
const PROD_ESTIMATED_REQS = PROD_RPS * PROD_DURATION_S;

if (ALVO === "producao") {
  if (
    !Number.isFinite(PROD_VUS) ||
    PROD_VUS < 1 ||
    PROD_VUS > ADR0012_MAX_VUS ||
    !Number.isFinite(PROD_RPS) ||
    PROD_RPS < 1 ||
    PROD_RPS > ADR0012_MAX_RPS ||
    !Number.isFinite(PROD_DURATION_S) ||
    PROD_DURATION_S < 1 ||
    PROD_ESTIMATED_REQS > ADR0012_MAX_REQS
  ) {
    throw new Error(
      `ABORTADO (ADR 0012): parâmetros excedem teto de produção (VUs=${PROD_VUS}<=${ADR0012_MAX_VUS}, RPS=${PROD_RPS}<=${ADR0012_MAX_RPS}, total=${PROD_ESTIMATED_REQS}<=${ADR0012_MAX_REQS}).`,
    );
  }
}

const SUMMARY_OUT = (__ENV.K6_SUMMARY_OUT || "/out/k6-resumo-cp4.json").trim();

// =============================================================================
// Configuração de execução e thresholds
// =============================================================================
export const options =
  ALVO === "local"
    ? {
        vus: 20,
        duration: "60s",
        thresholds: {
          http_req_failed: ["rate<0.01"],
          "http_req_duration{tipo:leitura}": ["p(95)<800"],
          "http_req_duration{tipo:escrita}": ["p(95)<1500"],
        },
      }
    : {
        scenarios: {
          fumaca_producao_adr0012: {
            executor: "constant-arrival-rate",
            rate: PROD_RPS,
            timeUnit: "1s",
            duration: `${PROD_DURATION_S}s`,
            preAllocatedVUs: PROD_VUS,
            maxVUs: PROD_VUS,
          },
        },
        thresholds: {
          http_req_failed: ["rate<0.01"],
          "http_reqs{tipo:leitura}": [`count<=${ADR0012_MAX_REQS}`],
          "http_req_duration{tipo:leitura}": ["p(95)<800"],
        },
      };

// =============================================================================
// Utilitários locais
// =============================================================================
const PDF_FICTICIO =
  "%PDF-1.4\n% DOCUMENTO FICTICIO — TESTE DE CARGA AUTOMATIZADO HABILITA\n" +
  "1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n";

function autenticarEmulador(email, senha) {
  const url = `${AUTH_EMULATOR_URL}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=demo-habilita-key`;
  const resp = http.post(
    url,
    JSON.stringify({
      email,
      password: senha,
      returnSecureToken: true,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (resp.status !== 200) {
    fail(`Falha ao autenticar '${email}' no Auth Emulator: HTTP ${resp.status} - ${resp.body}`);
  }
  const body = resp.json();
  if (!body || !body.idToken) {
    fail(`Auth Emulator não retornou idToken para '${email}'.`);
  }
  return body.idToken;
}

function gerarCpfFicticio(semente) {
  const base = String(300000000 + (semente % 599999999)).padStart(9, "0");
  const d = base.split("").map((n) => Number(n));
  if (d.every((x) => x === d[0])) {
    d[8] = (d[8] + 1) % 10;
  }
  let s1 = 0;
  for (let i = 0; i < 9; i += 1) {
    s1 += d[i] * (10 - i);
  }
  const r1 = s1 % 11;
  const dv1 = r1 < 2 ? 0 : 11 - r1;
  d.push(dv1);

  let s2 = 0;
  for (let i = 0; i < 10; i += 1) {
    s2 += d[i] * (11 - i);
  }
  const r2 = s2 % 11;
  const dv2 = r2 < 2 ? 0 : 11 - r2;
  d.push(dv2);
  return d.join("");
}

// =============================================================================
// Setup: autentica via Firebase Auth Emulator e prepara recursos isolados
// =============================================================================
export function setup() {
  if (ALVO === "producao") {
    const tokenProd = (__ENV.FIREBASE_TOKEN || "").trim();
    if (!tokenProd) {
      fail("ALVO=producao requer __ENV.FIREBASE_TOKEN pré-obtido para fumaça de leitura.");
    }
    return {
      alvo: ALVO,
      tokenAdmin: tokenProd,
      tokenTerc: tokenProd,
      tokenAnalista: tokenProd,
      tokenPorteiro: tokenProd,
      contratoCargaId: null,
      trabalhadorCargaId: null,
    };
  }

  const demoPass = (__ENV.DEMO_PASSWORD || "SenhaDemo123!").trim();
  const tokenAdmin = autenticarEmulador("admin.alfa@demo.habilita.test", demoPass);
  const tokenTerc = autenticarEmulador("terceirizada1.alfa@demo.habilita.test", demoPass);
  const tokenAnalista = autenticarEmulador("analista.alfa@demo.habilita.test", demoPass);
  const tokenPorteiro = autenticarEmulador("porteiro.alfa@demo.habilita.test", demoPass);

  // Cria contrato dedicado ao teste de carga para não mutar o seed canônico ctr_alfa_1
  const respCtr = http.post(
    `${BASE_URL}/contratos`,
    JSON.stringify({
      identificador: `CTR-CARGA-${Date.now()}`,
      terceirizadaId: "ter_alfa_1",
      dataInicio: "2026-01-01",
      dataFim: "2027-12-31",
    }),
    {
      headers: {
        Authorization: `Bearer ${tokenAdmin}`,
        "Content-Type": "application/json",
      },
    },
  );
  if (respCtr.status !== 201) {
    fail(`Falha ao preparar contrato de carga no setup: HTTP ${respCtr.status} - ${respCtr.body}`);
  }
  const contratoCargaId = respCtr.json("id");

  // Cria trabalhador dedicado para uploads concorrentes sem inflar coleção de trabalhadores
  const cpfSetup = gerarCpfFicticio(99991);
  const respTrb = http.post(
    `${BASE_URL}/trabalhadores`,
    JSON.stringify({
      nome: "Trabalhador Carga K6",
      cpf: cpfSetup,
    }),
    {
      headers: {
        Authorization: `Bearer ${tokenTerc}`,
        "Content-Type": "application/json",
      },
    },
  );
  if (respTrb.status !== 201) {
    fail(`Falha ao preparar trabalhador de carga no setup: HTTP ${respTrb.status} - ${respTrb.body}`);
  }
  const trabalhadorCargaId = respTrb.json("id");

  return {
    alvo: ALVO,
    tokenAdmin,
    tokenTerc,
    tokenAnalista,
    tokenPorteiro,
    contratoCargaId,
    trabalhadorCargaId,
  };
}

// =============================================================================
// Execução por VU
// =============================================================================
export default function (data) {
  if (data.alvo === "producao") {
    const hdr = { Authorization: `Bearer ${data.tokenAdmin}` };
    const r = http.get(`${BASE_URL}/me`, {
      headers: hdr,
      tags: { tipo: "leitura", rota: "GET /me" },
    });
    if (r.status === 429 || r.status >= 500) {
      exec.test.abort(`ADR 0012: resposta ${r.status} recebida em produção; abortando.`);
    }
    check(r, { "GET /me 200": (res) => res.status === 200 });
    return;
  }

  const headersAdmin = { Authorization: `Bearer ${data.tokenAdmin}` };
  const headersTerc = { Authorization: `Bearer ${data.tokenTerc}` };
  const headersAnalista = { Authorization: `Bearer ${data.tokenAnalista}` };
  const headersPorteiro = { Authorization: `Bearer ${data.tokenPorteiro}` };

  // 1. Leituras (tag tipo:leitura, threshold p95 < 800 ms)
  const rMe = http.get(`${BASE_URL}/me`, {
    headers: headersPorteiro,
    tags: { tipo: "leitura", rota: "GET /me" },
  });
  check(rMe, { "GET /me (porteiro) == 200": (r) => r.status === 200 });

  const rContratos = http.get(`${BASE_URL}/contratos?limite=20`, {
    headers: headersAdmin,
    tags: { tipo: "leitura", rota: "GET /contratos" },
  });
  check(rContratos, { "GET /contratos == 200": (r) => r.status === 200 });

  const rTrbDetalhe = http.get(`${BASE_URL}/trabalhadores/trb_alfa_1`, {
    headers: headersTerc,
    tags: { tipo: "leitura", rota: "GET /trabalhadores/{id}" },
  });
  check(rTrbDetalhe, { "GET /trabalhadores/trb_alfa_1 == 200": (r) => r.status === 200 });

  const rDocsFila = http.get(`${BASE_URL}/documentos?limite=20`, {
    headers: headersAnalista,
    tags: { tipo: "leitura", rota: "GET /documentos" },
  });
  check(rDocsFila, { "GET /documentos == 200": (r) => r.status === 200 });

  // 2. Escritas (tag tipo:escrita, threshold p95 < 1500 ms)
  // Alterna entre geração de URL assinada, atualização de matriz e upload+aprovação
  const ciclo = __ITER % 3;

  if (ciclo === 0) {
    const rSigned = http.post(`${BASE_URL}/documentos/doc_alfa_1_aso/url-download`, null, {
      headers: headersTerc,
      tags: { tipo: "escrita", rota: "POST /documentos/{id}/url-download" },
    });
    check(rSigned, { "POST /documentos/{id}/url-download == 200": (r) => r.status === 200 });
  } else if (ciclo === 1) {
    const rExig = http.put(
      `${BASE_URL}/contratos/${data.contratoCargaId}/exigencias`,
      JSON.stringify({ tipoDocumentoIds: ["tdoc_aso", "tdoc_nr35"] }),
      {
        headers: {
          ...headersAdmin,
          "Content-Type": "application/json",
        },
        tags: { tipo: "escrita", rota: "PUT /contratos/{id}/exigencias" },
      },
    );
    check(rExig, { "PUT /contratos/{id}/exigencias == 200": (r) => r.status === 200 });
  } else {
    const rUpload = http.post(
      `${BASE_URL}/trabalhadores/${data.trabalhadorCargaId}/documentos`,
      {
        arquivo: http.file(PDF_FICTICIO, "documento_carga.pdf", "application/pdf"),
        tipoDocumentoId: "tdoc_aso",
        validoAte: "2027-12-31",
        dataEmissao: "2026-01-15",
      },
      {
        headers: headersTerc,
        tags: { tipo: "escrita", rota: "POST /trabalhadores/{id}/documentos" },
      },
    );
    const uploadOk = check(rUpload, {
      "POST /trabalhadores/{id}/documentos == 201": (r) => r.status === 201,
    });
    if (uploadOk) {
      const docId = rUpload.json("id");
      const rAprov = http.post(
        `${BASE_URL}/documentos/${docId}/aprovacao`,
        JSON.stringify({ validoAte: "2027-12-31" }),
        {
          headers: {
            ...headersAnalista,
            "Content-Type": "application/json",
          },
          tags: { tipo: "escrita", rota: "POST /documentos/{id}/aprovacao" },
        },
      );
      check(rAprov, { "POST /documentos/{id}/aprovacao == 200": (r) => r.status === 200 });
    }
  }

  sleep(0.2);
}

// =============================================================================
// Resumo estruturado para evidência do CP4
// =============================================================================
export function handleSummary(data) {
  const metricaLeitura = data.metrics["http_req_duration{tipo:leitura}"];
  const metricaEscrita = data.metrics["http_req_duration{tipo:escrita}"];
  const metricaGlobal = data.metrics.http_req_duration;
  const metricaReqs = data.metrics.http_reqs;
  const metricaFalhas = data.metrics.http_req_failed;

  const p95Leitura = metricaLeitura && metricaLeitura.values ? metricaLeitura.values["p(95)"] : null;
  const p95Escrita = metricaEscrita && metricaEscrita.values ? metricaEscrita.values["p(95)"] : null;
  const p95Global = metricaGlobal && metricaGlobal.values ? metricaGlobal.values["p(95)"] : null;
  const totalReqs = metricaReqs && metricaReqs.values ? metricaReqs.values.count : 0;
  const rps = metricaReqs && metricaReqs.values ? metricaReqs.values.rate : 0;
  const taxaFalhas = metricaFalhas && metricaFalhas.values ? metricaFalhas.values.rate : 0;

  const thresholdsAprovados = Object.values(data.metrics).every((metrica) => {
    if (!metrica || !metrica.thresholds) {
      return true;
    }
    return Object.values(metrica.thresholds).every((t) => t.ok === true);
  });

  const resumo = {
    alvo: ALVO,
    baseUrl: BASE_URL,
    executadoEm: new Date().toISOString(),
    configuracao:
      ALVO === "local"
        ? { vus: 20, duration: "60s" }
        : {
            adr: "ADR-0012",
            vus: PROD_VUS,
            rps: PROD_RPS,
            duration: `${PROD_DURATION_S}s`,
            tetoRequisicoes: ADR0012_MAX_REQS,
          },
    resumo: {
      totalRequisicoes: totalReqs,
      rps: Number((rps || 0).toFixed(2)),
      taxaErro: Number((taxaFalhas || 0).toFixed(6)),
      p95LeituraMs: p95Leitura !== null ? Number(p95Leitura.toFixed(2)) : null,
      p95EscritaMs: p95Escrita !== null ? Number(p95Escrita.toFixed(2)) : null,
      p95GlobalMs: p95Global !== null ? Number(p95Global.toFixed(2)) : null,
      thresholdsAprovados,
    },
    metrics: data.metrics,
    root_group: data.root_group,
  };

  return {
    [SUMMARY_OUT]: JSON.stringify(resumo, null, 2),
    stdout: textSummary(data, { indent: " ", enableColors: false }),
  };
}
