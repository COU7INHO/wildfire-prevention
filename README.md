# plano-v2 — priorização da prevenção de incêndios

Ferramenta de apoio à decisão para municípios: **onde a gestão de combustível
protege mais**, com dados abertos e oficiais, atualizados automaticamente.

Piloto: **Baião** (tipologia T4 do ICNF — o pior escalão: muitas ocorrências e
muita área ardida).

Não é um simulador de propagação nem substitui o PMDFCI. É a camada de decisão
por cima: risco × exposição × dificuldade de combate, ordenada e explicada.

---

## Arrancar

```bash
make setup     # uma vez: uv sync, npm install, libomp
make app       # http://localhost:5175
make estado    # o que está construído e quão recente é
```

Reconstruir tudo do zero (horas — descarrega ~10 GB de satélite):

```bash
make dados
```

`make help` lista tudo.

---

## O que a aplicação mostra

| Vista | Responde a | Atualiza |
|---|---|---|
| **Onde atuar** | onde a prevenção protege mais (risco × exposição × dificuldade) | anual |
| **Onde arde** | propensão estrutural, com as áreas ardidas reais sobreponíveis por ano | anual |
| **Vegetação** | tipo de ocupação, com espécie (eucalipto / pinheiro / folhosas) | ~5 anos |
| **Secura** | estado atual da vegetação e **anomalia** face ao mesmo mês em 2015-2025 | semanal |
| **Plano 2021** | comparação lado a lado com a cartografia oficial do PMDFCI | — |

Camadas de contexto em qualquer vista: casas, estradas, pontos de água oficiais,
ignições do ano em curso.

---

## Fontes de dados

| Dado | Fonte | Idade típica |
|---|---|---|
| Áreas ardidas | ICNF (ArcGIS REST, uma camada por ano) | 2009-2025 |
| Ignições do ano | Proteção Civil via `api.fogos.pt` | tempo quase real |
| Terreno (declive, exposição) | AWS Terrain Tiles ~29 m | estável |
| Ocupação do solo | COS 2015 / 2018 / 2023 (DGT) | plurianual |
| Vegetação (NDVI/NDMI) | Sentinel-2 L2A via Copernicus (CDSE) | ~5 dias |
| Edifícios | Microsoft Global ML Building Footprints | 18 914 em Baião |
| Pontos de água | **PMDFCI do próprio município** (RPA, 2G+3G) | 120 pontos |
| Bombeiros, estradas | OpenStreetMap | contínua |
| Perigosidade oficial | **PMDFCI 2021-2030**, raster 10 m | fixa até 2030 |

O acesso ao Sentinel exige credenciais Copernicus em `.env`
(`CDSE_USERNAME` / `CDSE_PASSWORD`) — ficheiro ignorado pelo git.

---

## O modelo

**LightGBM** sobre um painel de 2,1 M linhas (210 998 células × 10 anos). Cada
linha usa vegetação, combustível e histórico **anteriores** ao ano que prevê.

Hiperparâmetros afinados com validação separada do teste
(treino ≤2019 · validação 2020-2021 · teste 2022-2025, tocado uma só vez).
A regularização forte (`min_child_samples=2000`) é essencial: sem ela o modelo
decora células em vez de aprender padrões.

### Validação honesta

| Teste | AUC |
|---|---|
| Anos nunca vistos (2023 / 2024 / 2025) | **0,804** |
| Anos **e terreno** nunca vistos (metade do concelho) | **0,763** |
| Confronto justo com a cartografia oficial (2022-2025) | 0,681 vs 0,700 |

**A diferença para a cartografia oficial NÃO é estatisticamente significativa**
(IC 95% [−0,020, +0,069], bootstrap por blocos espaciais). Os dois são
equivalentes dentro da margem de erro.

O diferenciador não é a precisão — é que a cartografia oficial está **fixa até
2030** e este modelo **re-treina**.

### O modelo é um artefacto identificável

`make retreinar` guarda o modelo em `data/out/modelo_<municipio>.txt` e regista
em `.json` **quando foi treinado, com que anos, quantas linhas e que variáveis**.
A atualização semanal pontua com esse modelo guardado, em vez de treinar um novo
de cada vez.

Isto existe por uma razão concreta: um mapa publicado serve de base a decisões
de despesa pública. Tem de ser possível responder, meses depois, *qual* modelo
produziu o mapa em que se decidiu. `make estado` mostra sempre qual está em uso.

---

## Atualização automática

```bash
make atualizar    # ignições + áreas ardidas + secura + exportar  (~3 min)
make cron         # instruções para agendar semanalmente
make retreinar    # re-treinar o modelo (após cada época de fogos)
```

Cada passo tolera falhas: uma fonte em baixo não impede as restantes, e a
aplicação continua a servir os últimos dados bons.

---

## Colocar online

```bash
make build     # interface de produção -> webapp/dist/
make nginx     # imprime a configuração do servidor
make cron      # imprime a linha do crontab
```

**Atenção a isto**, é o erro fácil de cometer: o `dist/` só recebe os dados no
momento do build. Se o nginx servir os dados de dentro do `dist/`, o cron
atualiza os ficheiros e **o site continua a mostrar os antigos, sem avisar**.

Por isso o `/data/` é servido diretamente de `webapp/public/data/`, que é onde o
cron escreve. Assim os dados atualizam sem reconstruir a interface, e a
interface reconstrói sem tocar nos dados.

O servidor precisa de:

| | |
|---|---|
| `.env` | credenciais Copernicus |
| `uv` | no `PATH` do cron (a linha impressa já o define) |
| `data/out` + partes de `data/cache` | ~200 MB — **não** são precisos os 14 GB de bandas de satélite |

As bandas só são necessárias para reconstruir composições antigas; o cron
descarrega o que precisar do mês em curso.

---

## Limitações conhecidas

- **Um só município.** O código tem Baião fixo em vários sítios (tile Sentinel
  `T29TNF`, código DICO `1302`, tile GHSL). `make dados MUN=X` não funciona
  ainda para outro concelho.
- **Viés de supressão.** Os dados mostram onde ardeu, não onde os bombeiros
  travaram o fogo. Nenhum modelo de incêndios escapa a isto.
- **Sem meteorologia.** Não prevemos um dia concreto — só propensão estrutural.
  Foi por isto que a vista de "prontidão sazonal" foi removida: sem vento,
  temperatura e FWI, o sinal não era fiável.
- **A secura é descritiva, não preditiva.** Diz o que o satélite mediu, não o
  que vai arder.
- **Resolução ~29 m.** Faixas de gestão de combustível (mediana 0,09 ha) são
  demasiado estreitas para monitorizar por satélite.
- **Ocupação do solo de 2023**, anterior ao grande incêndio de 2024.

---

## Estrutura

```
plano_v2/
  boundary.py        fronteira do município (OSM)
  features.py        grelha de células: terreno + COS + histórico ICNF
  access.py          casas, estradas, água, bombeiros → colunas da grelha
  veg_panel.py       painel de vegetação por ano (Sentinel)
  monthly_archive.py composições mensais 2015-2025 (base da anomalia)
  seca_history.py    meses recentes de secura (janela deslizante)
  anomalia.py        secura face ao mesmo mês em anos anteriores
  panel_model.py     modelo, hiperparâmetros afinados, suscetibilidade
  tune.py            busca de hiperparâmetros com validação separada
  priority.py        prioridade = risco × exposição × dificuldade
  plano_oficial.py   perigosidade do PMDFCI + confronto com incerteza
  export_web.py      GeoJSON que a aplicação lê
  atualizar.py       o que o cron corre
  estado.py          make estado
webapp/              React + MapLibre (interface em português)
data/cache/          descarregado (~10 GB, fora do git)
data/out/            produtos intermédios
```

---

## Princípio de trabalho

Medir antes de afirmar. Ao longo do desenvolvimento, várias conclusões
aparentemente boas caíram quando testadas — e ficaram registadas aqui em vez de
serem escondidas: a comparação enviesada com o plano, o modelo de "prontidão"
que respondia bem à pergunta errada, a valoração económica com 43% de lacunas.

Se um número neste README parecer bom demais, o teste que o produziu está no
código.
