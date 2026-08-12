# wildfire-prevention — priorização da prevenção de incêndios (piloto: Baião)
#
# Uso diário:
#   make app          arranca a aplicação
#   make refresh    refresca os data que mudam (o que o cron corre)
#
# Do zero, numa máquina nova:
#   make setup && make data && make app

MUN ?= Baião
PY  := uv run python

.PHONY: help setup data grid access sentinel archive archive-patient dryness \
        travel-times model tune maps compare export hero dryness-images dryness-animation app build stop \
        refresh retrain cron nginx status clean

help:
	@echo "wildfire-prevention — priorização da prevenção de incêndios ($(MUN))"
	@echo ""
	@echo "  ARRANCAR"
	@echo "    make setup        instala tudo (uv sync, npm, libomp)"
	@echo "    make app          aplicação em http://localhost:5175"
	@echo "    make stop        pára a aplicação"
	@echo "    make status       o que está construído e quão recente é"
	@echo ""
	@echo "  DADOS (ordem de dependência)"
	@echo "    make data        pipeline completo, do zero"
	@echo "    make grid       grid de células: terreno + COS + histórico ICNF"
	@echo "    make access      casas (MS), estradas, água (PMDFCI), bombeiros"
	@echo "    make travel-times       tempo de viagem dos bombeiros por estrada real"
	@echo "    make sentinel     painel de vegetação por ano (10 anos)"
	@echo "    make archive      composições mensais 2015-2025 (base da anomaly)"
	@echo "    make dryness       meses recentes de dryness"
	@echo ""
	@echo "  MODELO"
	@echo "    make model       treina e valida (imprime AUC honesto)"
	@echo "    make tune       busca de hiperparâmetros (validação separada)"
	@echo "    make compare     confronto com a cartografia oficial do PMDFCI"
	@echo "    make maps        PNG de diagnóstico: previsto vs. ardido"
	@echo ""
	@echo "  PRODUÇÃO"
	@echo "    make export     gera os GeoJSON que a aplicação lê"
	@echo "    make hero        imagem de fundo da landing (satélite do concelho)"
	@echo "    make dryness-images  uma imagem por mês de dryness, prontas para animar"
	@echo "    make dryness-animation     anima os meses: MP4 na landing, GIF no Desktop"
	@echo "    make refresh    refresca ignições + fogos + dryness + exporta"
	@echo "    make retrain    re-treina o model e exporta (após época de fogos)"
	@echo ""
	@echo "  COLOCAR ONLINE"
	@echo "    make build        build de produção da interface"
	@echo "    make nginx        configuração do servidor (atenção ao /data/)"
	@echo "    make cron         agendar a atualização semanal"
	@echo ""
	@echo "  Município: MUN=$(MUN)"

# ---------------------------------------------------------------- instalação
setup:
	brew list libomp >/dev/null 2>&1 || brew install libomp
	uv sync
	cd webapp && npm install

# ---------------------------------------------------------------------- data
# Ordem obrigatória: a grid cria o ficheiro de features que os restantes
# passos vão preenchendo (access escreve colunas, sentinel escreve o painel).
data: grid access travel-times sentinel archive dryness export

grid:
	$(PY) -m wildfire_prevention.features $(MUN)

access:
	$(PY) -m wildfire_prevention.access $(MUN)

# tempo de viagem dos bombeiros por rota real (OSRM), não por linha reta
travel-times:
	$(PY) -m wildfire_prevention.response_time $(MUN)

sentinel:
	$(PY) -m wildfire_prevention.veg_panel $(MUN)

archive:
	$(PY) -m wildfire_prevention.monthly_archive $(MUN)

# o Copernicus bloqueia a conta se abrirmos demasiadas sessões; esta variante
# espera que desbloqueie e retoma sozinha (útil para o archive completo)
archive-patient:
	$(PY) -m wildfire_prevention.run_archive $(MUN)

dryness:
	$(PY) -m wildfire_prevention.dryness_history $(MUN)

# --------------------------------------------------------------------- model
model:
	$(PY) -m wildfire_prevention.panel_model $(MUN)

tune:
	$(PY) -m wildfire_prevention.tune $(MUN)

# PNG de diagnóstico: previsto vs. o que ardeu mesmo
maps:
	$(PY) -m wildfire_prevention.map_render $(MUN)

compare:
	$(PY) -c "from wildfire_prevention.official_plan import head_to_head; \
	          from wildfire_prevention.export_web import export_comparison; \
	          import json; print(json.dumps(head_to_head('$(MUN)'), indent=2, ensure_ascii=False)); \
	          export_comparison('$(MUN)')"

# ------------------------------------------------------------------ produção
export:
	$(PY) -m wildfire_prevention.export_web $(MUN)

# imagem de fundo da landing, a partir dos tiles de satélite do próprio concelho
hero:
	$(PY) -m wildfire_prevention.landing_hero $(MUN)

# uma imagem por mês de dryness, com escala partilhada, prontas para animar
# (OUT=/outro/sitio para não usar o Desktop)
OUT ?= $(HOME)/Desktop
dryness-images:
	$(PY) -m wildfire_prevention.dryness_frames $(MUN) "$(OUT)"

# anima os meses: MP4 + poster para a landing, GIF para partilhar
dryness-animation:
	$(PY) -m wildfire_prevention.dryness_animation $(MUN) "$(OUT)"

refresh:
	$(PY) -m wildfire_prevention.refresh $(MUN)

# re-treina e GUARDA o model, depois reavalia e exporta. O model guardado é
# o que a atualização semanal usa — assim o mapa publicado tem sempre um
# model identificável por trás, com data e parâmetros registados.
retrain:
	$(PY) -c "from wildfire_prevention.panel_model import train; train('$(MUN)')"
	$(MAKE) model compare export

app:
	cd webapp && npm run dev

# build de produção da interface (os data NÃO são copiados para dist —
# o nginx serve /data/ diretamente de webapp/public/data, para o cron poder
# refresh sem reconstruir a interface)
build:
	cd webapp && npm run build
	@echo ""
	@echo "interface construída em webapp/dist/"
	@echo "configuração do nginx:  make nginx"

stop:
	@lsof -ti :5175 | xargs kill 2>/dev/null || true
	@echo "aplicação parada"

# ------------------------------------------------------------------ operação
cron:
	@echo "Agendar a atualização semanal (segundas às 6h)."
	@echo "O cron arranca com um PATH mínimo, por isso é preciso indicá-lo:"
	@echo ""
	@echo "  crontab -e   e acrescentar:"
	@echo ""
	@echo "  PATH=$(dir $(shell command -v uv)):/usr/bin:/bin"
	@echo "  0 6 * * 1 cd $(PWD) && $(shell command -v make) refresh >> $(PWD)/data/cron.log 2>&1"
	@echo ""
	@echo "  confirmar que ficou:  crontab -l"
	@echo "  ver o registo:        tail -f $(PWD)/data/cron.log"
	@echo ""
	@echo "  Requisitos no servidor: .env com as credenciais Copernicus,"
	@echo "  uv instalado, e ~200 MB em data/ (não são precisos os 14 GB de cache)."

nginx:
	@echo "Servir a aplicação. O ponto crítico: /data/ é servido DIRETAMENTE de"
	@echo "public/data, para o cron refresh sem reconstruir a interface."
	@echo ""
	@echo "server {"
	@echo "    listen 80;"
	@echo "    server_name  o-teu-dominio.pt;"
	@echo ""
	@echo "    root $(PWD)/webapp/dist;"
	@echo "    index index.html;"
	@echo "    # \$$uri.html serves /mapa as well as /mapa.html"
	@echo "    location / { try_files \$$uri \$$uri.html \$$uri/ /index.html; }"
	@echo ""
	@echo "    # data atualizados pelo cron, fora do build"
	@echo "    location /data/ {"
	@echo "        alias $(PWD)/webapp/public/data/;"
	@echo "        add_header Cache-Control \"no-cache\";"
	@echo "    }"
	@echo "}"
	@echo ""
	@echo "  make build   antes do primeiro arranque e sempre que a interface mudar"

status:
	@$(PY) -m wildfire_prevention.status $(MUN)

clean:
	@echo "isto apaga só os produtos, nunca a cache de downloads"
	rm -f data/out/*.png webapp/public/data/*.geojson
