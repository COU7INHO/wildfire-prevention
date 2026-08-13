// Two languages, one product. The Portuguese string IS the key: there is no
// invented key namespace to keep in sync, and a missing translation falls back
// to Portuguese rather than rendering "sidebar.title.label".
//
// Language comes from ?lang= (how the English landing links here), then from
// what was chosen before, then Portuguese, because that is who this is for.

const EN = {
  // ---- chrome -----------------------------------------------------------
  "Município de Baião": "Municipality of Baião",
  "Prevenção de Incêndio Rural": "Rural Fire Prevention",
  "Página inicial": "Home",
  "Apoio à decisão na gestão de combustível: onde intervir primeiro, a partir do terreno, da vegetação, do histórico de incêndios e da exposição de habitações.":
    "Decision support for fuel management: where to act first, from terrain, vegetation, fire history and the exposure of homes.",
  "Abrir menu": "Open menu",
  Fechar: "Close",
  "Fechar menu": "Close menu",

  // ---- views ------------------------------------------------------------
  "Ver no mapa": "Show on the map",
  "Onde atuar": "Where to act",
  "Onde arde": "Where it burns",
  Vegetação: "Vegetation",
  Secura: "Dryness",
  "Plano 2021": "2021 Plan",
  "Risco, valor exposto e dificuldade de combate: onde a prevenção protege mais.":
    "Risk, exposed value and suppression difficulty: where prevention protects most.",
  "Propensão estrutural: onde arde de forma recorrente, ao longo dos anos.":
    "Structural propensity: where fire returns, year after year.",

  // ---- controls ---------------------------------------------------------
  "Zonas em alerta:": "Alert zones:",
  "do concelho": "of the municipality",
  Edifícios: "Buildings",
  "Estradas e caminhos": "Roads and tracks",
  "Pontos de água": "Water points",
  "Sobrepor área ardida real (ICNF):": "Overlay real burned area (ICNF):",
  "Sobrepor os incêndios reais de:": "Overlay the real fires of:",
  "Nenhum ano": "No year",
  Ano: "Year",

  // ---- legend -----------------------------------------------------------
  "Prioridade de intervenção": "Intervention priority",
  "Muito alta (top 5% do concelho)": "Very high (top 5% of the municipality)",
  "Alta (top 10%)": "High (top 10%)",
  "Média (top 20%)": "Medium (top 20%)",
  Baixa: "Low",
  "Muito alta (top 5%)": "Very high (top 5%)",
  "Alta (top 15%)": "High (top 15%)",
  "Média (top 30%)": "Medium (top 30%)",
  "Propensão estrutural": "Structural propensity",

  // ---- dryness ----------------------------------------------------------
  "Como está a vegetação agora?": "How is the vegetation right now?",
  "Evolução da secura (0 = húmido, 100 = muito seco)":
    "Dryness over time (0 = moist, 100 = very dry)",
  "mais seco que o normal": "drier than usual",
  "Secura da vegetação": "Vegetation dryness",
  "Secura vs. o normal": "Dryness vs. normal",
  atual: "current",
  "Muito seco": "Very dry",
  Seco: "Dry",
  Moderado: "Moderate",
  Húmido: "Moist",
  "A ocupação do solo é de 2023, anterior aos incêndios de 2024.":
    "Land cover is from 2023, before the 2024 fires.",

  // ---- cell card --------------------------------------------------------
  "Porque prevenir aqui?": "Why prevent here?",
  Prioridade: "Priority",
  "Muito alta": "Very high",
  Alta: "High",
  Média: "Medium",
  Inclinação: "Slope",
  "Ardeu (2009-25)": "Burned (2009-25)",
  "Casas (250 m)": "Homes (250 m)",
  "Água a": "Water at",
  "Estrada a": "Road at",
  "Bombeiros a": "Fire station",
  Ardeu: "Burned",
  "Casas mesmo ao lado": "Homes right beside it",
  "Habitações próximas": "Homes nearby",
  "Alguma exposição a habitações": "Some exposure to homes",
  "Difícil de combater (água longe)": "Hard to fight (water far off)",
  "Longe de habitações": "Far from homes",

  // ---- comparison -------------------------------------------------------
  "Modelo atualizado": "Updated model",
  "Plano oficial 2021": "Official 2021 plan",
  "Isto confirma o plano municipal?": "Does this confirm the municipal plan?",
  "Duas camadas, variáveis diferentes": "Two layers, different variables",
  Modelo: "Model",
  Plano: "Plan",
  "Os dois": "Both",
  "acertam de forma semelhante": "score alike",
  "acertam de forma equivalente, dentro da margem de erro":
    "score equivalently, within the margin of error",
  "cartografia oficial": "official map",
  ", que atravessa o zero, pelo que a diferença não é significativa":
    ", which crosses zero, so the difference is not significant",

  // ---- methodology ------------------------------------------------------
  Metodologia: "Methodology",
  "Como é calculado o risco e a prioridade": "How risk and priority are computed",
  "A que perguntas responde": "What it answers",
  "Onde intervir primeiro?": "Where to act first?",
  "Que zonas ardem de forma recorrente?": "Which areas burn again and again?",
  "O que se estima como risco": "What is estimated as risk",
  "Risco de arder": "Risk of burning",
  "dificuldade de combate": "suppression difficulty",
  "Como foi treinado": "How it was trained",
  "Como foi verificado": "How it was checked",
  "O que isto não faz": "What this does not do",
  "Projeto em desenvolvimento.": "Work in progress.",
  "são usadas": "are used",
  anterior: "earlier",
  depois: "later",

  // ---- sources ----------------------------------------------------------
  "Fontes de dados": "Data sources",
  "Prevenção de Incêndio — Baião": "Rural Fire Prevention — Baião",
  o_que: "What",
  quem: "Who",
  quando: "When",
};

// The exported GeoJSON carries Portuguese fuel labels. The set is closed and
// defined once in the pipeline, so mapping the label is safe and needs no
// change to the data contract.
const FUEL_EN = {
  Eucalipto: "Eucalyptus",
  "Resinosas (pinheiro)": "Conifers (pine)",
  "Folhosas (carvalho, castanheiro)": "Broadleaf (oak, chestnut)",
  Matos: "Brush",
  Agricultura: "Farmland",
  "Pastagens/agroflorestal": "Pasture / agroforestry",
  "Não combustível": "Non-combustible",
  "Solo descoberto": "Bare ground",
};

function resolve() {
  const q = new URLSearchParams(location.search).get("lang");
  if (q === "en" || q === "pt") {
    try { localStorage.setItem("lang", q); } catch { /* private mode */ }
    return q;
  }
  try { return localStorage.getItem("lang") === "en" ? "en" : "pt"; } catch { return "pt"; }
}

export const LANG = resolve();
export const isEN = LANG === "en";

document.documentElement.lang = isEN ? "en-US" : "pt-PT";
if (isEN) document.title = "Rural Fire Prevention — Baião";

export const t = (s) => (isEN ? EN[s] ?? s : s);
export const fuelLabel = (s) => (isEN ? FUEL_EN[s] ?? s : s);

/** Where the language switch should send the reader. */
export const otherLang = () => (isEN ? "?lang=pt" : "?lang=en");

/** 18 914 in Portuguese is 18,914 in English. */
export const num = (n, digits = 0) =>
  Number(n).toLocaleString(isEN ? "en-US" : "pt-PT", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
