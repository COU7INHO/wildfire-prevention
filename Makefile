# plano-v2 — priorização da prevenção de incêndios (piloto: Baião)
#
# Uso diário:
#   make app          arranca a aplicação
#   make atualizar    refresca os dados que mudam (o que o cron corre)
#
# Do zero, numa máquina nova:
#   make setup && make dados && make app

MUN ?= Baião
PY  := uv run python

.PHONY: help setup dados grelha acessos sentinel arquivo arquivo-paciente secura \
        tempos modelo afinar mapas comparar exportar app build parar \
        atualizar retreinar cron nginx estado limpar

help:
	@echo "plano-v2 — priorização da prevenção de incêndios ($(MUN))"
	@echo ""
	@echo "  ARRANCAR"
	@echo "    make setup        instala tudo (uv sync, npm, libomp)"
	@echo "    make app          aplicação em http://localhost:5175"
	@echo "    make parar        pára a aplicação"
	@echo "    make estado       o que está construído e quão recente é"
	@echo ""
	@echo "  DADOS (ordem de dependência)"
	@echo "    make dados        pipeline completo, do zero"
	@echo "    make grelha       grelha de células: terreno + COS + histórico ICNF"
	@echo "    make acessos      casas (MS), estradas, água (PMDFCI), bombeiros"
	@echo "    make tempos       tempo de viagem dos bombeiros por estrada real"
	@echo "    make sentinel     painel de vegetação por ano (10 anos)"
	@echo "    make arquivo      composições mensais 2015-2025 (base da anomalia)"
	@echo "    make secura       meses recentes de secura"
	@echo ""
	@echo "  MODELO"
	@echo "    make modelo       treina e valida (imprime AUC honesto)"
	@echo "    make afinar       busca de hiperparâmetros (validação separada)"
	@echo "    make comparar     confronto com a cartografia oficial do PMDFCI"
	@echo "    make mapas        PNG de diagnóstico: previsto vs. ardido"
	@echo ""
	@echo "  PRODUÇÃO"
	@echo "    make exportar     gera os GeoJSON que a aplicação lê"
	@echo "    make atualizar    refresca ignições + fogos + secura + exporta"
	@echo "    make retreinar    re-treina o modelo e exporta (após época de fogos)"
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

# ---------------------------------------------------------------------- dados
# Ordem obrigatória: a grelha cria o ficheiro de features que os restantes
# passos vão preenchendo (acessos escreve colunas, sentinel escreve o painel).
dados: grelha acessos tempos sentinel arquivo secura exportar

grelha:
	$(PY) -m plano_v2.features $(MUN)

acessos:
	$(PY) -m plano_v2.access $(MUN)

# tempo de viagem dos bombeiros por rota real (OSRM), não por linha reta
tempos:
	$(PY) -m plano_v2.tempo_resposta $(MUN)

sentinel:
	$(PY) -m plano_v2.veg_panel $(MUN)

arquivo:
	$(PY) -m plano_v2.monthly_archive $(MUN)

# o Copernicus bloqueia a conta se abrirmos demasiadas sessões; esta variante
# espera que desbloqueie e retoma sozinha (útil para o arquivo completo)
arquivo-paciente:
	$(PY) -m plano_v2.run_archive $(MUN)

secura:
	$(PY) -m plano_v2.seca_history $(MUN)

# --------------------------------------------------------------------- modelo
modelo:
	$(PY) -m plano_v2.panel_model $(MUN)

afinar:
	$(PY) -m plano_v2.tune $(MUN)

# PNG de diagnóstico: previsto vs. o que ardeu mesmo
mapas:
	$(PY) -m plano_v2.map_render $(MUN)

comparar:
	$(PY) -c "from plano_v2.plano_oficial import head_to_head; \
	          from plano_v2.export_web import export_comparison; \
	          import json; print(json.dumps(head_to_head('$(MUN)'), indent=2, ensure_ascii=False)); \
	          export_comparison('$(MUN)')"

# ------------------------------------------------------------------ produção
exportar:
	$(PY) -m plano_v2.export_web $(MUN)

atualizar:
	$(PY) -m plano_v2.atualizar $(MUN)

# re-treina e GUARDA o modelo, depois reavalia e exporta. O modelo guardado é
# o que a atualização semanal usa — assim o mapa publicado tem sempre um
# modelo identificável por trás, com data e parâmetros registados.
retreinar:
	$(PY) -c "from plano_v2.panel_model import train; train('$(MUN)')"
	$(MAKE) modelo comparar exportar

app:
	cd webapp && npm run dev

# build de produção da interface (os dados NÃO são copiados para dist —
# o nginx serve /data/ diretamente de webapp/public/data, para o cron poder
# atualizar sem reconstruir a interface)
build:
	cd webapp && npm run build
	@echo ""
	@echo "interface construída em webapp/dist/"
	@echo "configuração do nginx:  make nginx"

parar:
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
	@echo "  0 6 * * 1 cd $(PWD) && $(shell command -v make) atualizar >> $(PWD)/data/cron.log 2>&1"
	@echo ""
	@echo "  confirmar que ficou:  crontab -l"
	@echo "  ver o registo:        tail -f $(PWD)/data/cron.log"
	@echo ""
	@echo "  Requisitos no servidor: .env com as credenciais Copernicus,"
	@echo "  uv instalado, e ~200 MB em data/ (não são precisos os 14 GB de cache)."

nginx:
	@echo "Servir a aplicação. O ponto crítico: /data/ é servido DIRETAMENTE de"
	@echo "public/data, para o cron atualizar sem reconstruir a interface."
	@echo ""
	@echo "server {"
	@echo "    listen 80;"
	@echo "    server_name  o-teu-dominio.pt;"
	@echo ""
	@echo "    root $(PWD)/webapp/dist;"
	@echo "    index index.html;"
	@echo "    location / { try_files \$$uri \$$uri/ /index.html; }"
	@echo ""
	@echo "    # dados atualizados pelo cron, fora do build"
	@echo "    location /data/ {"
	@echo "        alias $(PWD)/webapp/public/data/;"
	@echo "        add_header Cache-Control \"no-cache\";"
	@echo "    }"
	@echo "}"
	@echo ""
	@echo "  make build   antes do primeiro arranque e sempre que a interface mudar"

estado:
	@$(PY) -m plano_v2.estado $(MUN)

limpar:
	@echo "isto apaga só os produtos, nunca a cache de downloads"
	rm -f data/out/*.png webapp/public/data/*.geojson
