import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { priorityLevel, explain } from "./explain.js";

// cache-buster: data files change between model runs; force the browser to refetch
const BUST = `?t=${Date.now()}`;
const DATA_URL = `/data/baiao_priority.geojson${BUST}`;

// Satellite basemap (Esri World Imagery) + place labels — no API key, demo use.
const STYLE = {
  version: 8,
  sources: {
    sat: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      attribution: "Imagery © Esri",
    },
    labels: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
    },
  },
  layers: [
    { id: "sat", type: "raster", source: "sat" },
    { id: "labels", type: "raster", source: "labels" },
  ],
};

// Priority is colored by PERCENTILE within the municipality: prevention budgets
// treat a few % of territory per year. The alert breadth (top X%) is user-set.
const priorityColor = (alert) => [
  "step", ["get", "pct"],
  "#3a9d4e",
  100 - 4 * alert, "#f2c200",
  100 - 2 * alert, "#e8710a",
  100 - alert, "#b3261e",
];

// coloured by percentile: relative fire-propensity across the municipality
const SUSC_COLOR = [
  "step", ["get", "susc_pct"],
  "#3a9d4e",
  70, "#f2c200",
  85, "#e8710a",
  95, "#b3261e",
];

const FUEL_COLOR = [
  "match", ["get", "fuel"],
  "Eucalipto", "#8e24aa",
  "Resinosas (pinheiro)", "#1b5e20",
  "Folhosas (carvalho, castanheiro)", "#66bb6a",
  "Matos", "#9e9d24",
  "Agricultura", "#d7a45c",
  "Pastagens/agroflorestal", "#aed581",
  "Não combustível", "#9e9e9e",
  "Solo descoberto", "#bcaaa4",
  "#8d6e63",
];

// current vegetation dryness (NDMI): low = dry = red, high = moist = green.
// Squares over water/urban (null) are hidden. Key selects the month (slider).
const drynessColorExpr = (key) => [
  "case", ["==", ["get", key], null], "rgba(0,0,0,0)",
  ["interpolate", ["linear"], ["get", key],
    0.05, "#b3261e",
    0.15, "#e8710a",
    0.25, "#f2c200",
    0.35, "#3a9d4e"],
];
const DRYNESS_COLOR = drynessColorExpr("ndmi");

// anomaly vs the same month in past years: negative = drier than usual (red),
// positive = wetter than usual (blue). Diverging scale centred on zero.
const anomColor = (key) => [
  "case", ["==", ["get", key], null], "rgba(0,0,0,0)",
  ["interpolate", ["linear"], ["get", key],
    -0.10, "#b3261e",
    -0.05, "#e8710a",
    -0.01, "#f2c200",
    0.01, "#e8eef4",
    0.05, "#4fc3f7"],
];

// official PMDFCI 2021 hazard surface, shown as percentile like ours so the two
// maps are directly comparable (their raster is a product of factors, not classes)
const OFFICIAL_COLOR = [
  "case", ["==", ["get", "oficial_pct"], null], "rgba(0,0,0,0)",
  ["step", ["get", "oficial_pct"],
    "#3a9d4e",
    70, "#f2c200",
    85, "#e8710a",
    95, "#b3261e"],
];

const VIEWS = {
  priority: { color: null, showTop: true, opacity: 0.55 }, // color from priorityColor(alert)
  susceptibility: { color: SUSC_COLOR, showTop: false, opacity: 0.55 },
  fuel: { color: FUEL_COLOR, showTop: false, opacity: 0.65 },
  secura: { color: DRYNESS_COLOR, showTop: false, opacity: 0.6 },
  oficial: { color: OFFICIAL_COLOR, showTop: false, opacity: 0.55 },
};

const OVERLAYS = ["ovl-estradas", "ovl-agua-fill", "ovl-agua-line", "ovl-agua-pt", "ovl-casas"];

function drynessLabel(i) {
  if (i >= 75) return "Muito seco";
  if (i >= 50) return "Seco";
  if (i >= 30) return "Moderado";
  return "Húmido";
}
function drynessColor(i) {
  if (i >= 75) return "#b3261e";
  if (i >= 50) return "#e8710a";
  if (i >= 30) return "#f2c200";
  return "#3a9d4e";
}

// Context overlays (houses, roads, water, ignitions, past fires). Shared by both
// panes so the comparison view carries the same context on either side.
function addContextLayers(map) {
  map.addSource("estradas", { type: "geojson", data: `/data/baiao_roads.geojson${BUST}` });
  map.addSource("agua", { type: "geojson", data: `/data/baiao_water.geojson${BUST}` });
  map.addSource("casas", { type: "geojson", data: `/data/baiao_buildings.geojson${BUST}` });
  map.addLayer({
    id: "ovl-estradas", type: "line", source: "estradas",
    layout: { visibility: "none" },
    paint: { "line-color": "#f5f5f5", "line-width": 1, "line-opacity": 0.85 },
  });
  map.addLayer({
    id: "ovl-agua-fill", type: "fill", source: "agua",
    filter: ["==", ["geometry-type"], "Polygon"],
    layout: { visibility: "none" },
    paint: { "fill-color": "#4fc3f7", "fill-opacity": 0.5 },
  });
  map.addLayer({
    id: "ovl-agua-line", type: "line", source: "agua",
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { visibility: "none" },
    paint: { "line-color": "#4fc3f7", "line-width": 2 },
  });
  map.addLayer({
    id: "ovl-agua-pt", type: "circle", source: "agua",
    filter: ["==", ["geometry-type"], "Point"],
    layout: { visibility: "none" },
    paint: {
      // official RPA styled like the plan's own legend: Mistos dark, Aéreos
      // amber, Terrestres mid blue; OSM leftovers light blue and small
      "circle-color": [
        "case",
        ["!=", ["get", "oficial"], true], "#4fc3f7",
        ["==", ["get", "classe"], "M"], "#0277bd",
        ["==", ["get", "classe"], "A"], "#fbc02d",
        "#039be5",
      ],
      "circle-radius": [
        "case",
        ["!=", ["get", "oficial"], true], 3.5,
        ["==", ["get", "classe"], "M"], 7,
        5,
      ],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.2,
    },
  });
  map.addLayer({
    id: "ovl-casas", type: "circle", source: "casas",
    layout: { visibility: "none" },
    paint: { "circle-color": "#ffd54f", "circle-radius": 2.5, "circle-stroke-color": "#5d4037", "circle-stroke-width": 0.8 },
  });

  // current-year ignition points (ANEPC via fires.pt)
  map.addSource("ignicoes", { type: "geojson", data: `/data/baiao_ignitions.geojson${BUST}` });
  map.addLayer({
    id: "ovl-ignicoes", type: "circle", source: "ignicoes",
    layout: { visibility: "none" },
    paint: {
      "circle-color": "#ff6d00",
      "circle-radius": 6,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.6,
    },
  });
  map.on("click", "ovl-ignicoes", (e) => {
    const p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false })
      .setLngLat(e.features[0].geometry.coordinates)
      .setHTML(
        `<strong>${p.natureza}</strong><br/>${p.data} às ${p.hora}<br/>${p.freguesia}` +
        (p.operacionais ? `<br/>${p.operacionais} operacionais` : "")
      )
      .addTo(map);
  });
  map.on("mouseenter", "ovl-ignicoes", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "ovl-ignicoes", () => (map.getCanvas().style.cursor = ""));

  // historical burnt-area perimeters (ICNF), filtered by year
  map.addSource("fogos", { type: "geojson", data: `/data/baiao_fires.geojson${BUST}` });
  map.addLayer({
    id: "ovl-fogos", type: "fill", source: "fogos",
    filter: ["==", ["get", "ano"], 0],
    paint: { "fill-color": "#ff3b30", "fill-opacity": 0.4, "fill-outline-color": "#ffffff" },
  });
}

function centroid(feature) {
  const ring = feature.geometry.coordinates[0];
  let x = 0, y = 0;
  for (let i = 0; i < 4; i++) { x += ring[i][0]; y += ring[i][1]; }
  return [x / 4, y / 4];
}

const cameraOf = (m) => ({
  center: m.getCenter(), zoom: m.getZoom(),
  bearing: m.getBearing(), pitch: m.getPitch(),
});

// Whole-municipality view: where every map starts and returns to on each view change
const WHOLE_MUNICIPALITY = { center: [-7.99, 41.17], zoom: 11.2, bearing: 0, pitch: 0 };

export default function App() {
  // Housekeeping (resize, re-align) also emits move events. Without this guard
  // they look like user panning and get echoed to the other pane, which is how
  // switching views used to snap the map back to an old zoom.
  const quiet = useRef(false);
  const quietly = (fn) => {
    quiet.current = true;
    try { fn(); } finally { requestAnimationFrame(() => { quiet.current = false; }); }
  };

  const mapRef = useRef(null);
  const containerRef = useRef(null);
  const map2Ref = useRef(null);       // right pane, only used in the "Plano 2021" view
  const container2Ref = useRef(null);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("priority");
  const [overlays, setOverlays] = useState({ buildings: false, roads: false, water: false, ignitions: false });
  const [alert, setAlert] = useState(5); // top X% of the municipality in red
  const [sentinelDate, setSentinelDate] = useState(null);
  const [dryness, setDryness] = useState(null);
  const [fireYear, setFireYear] = useState("");
  const [drynessSeries, setDrynessSeries] = useState([]);
  const [drynessMonth, setDrynessMonth] = useState(-1); // index into drynessSeries; -1 = latest month
  const [drynessMode, setDrynessMode] = useState("atual"); // "atual" | "anomaly"
  const [comparison, setComparison] = useState(null);
  const [anoComp, setAnoComp] = useState(""); // fire year overlaid on both panes
  const [map2Ready, setMap2Ready] = useState(0);
  const [sources, setSources] = useState([]);
  const [model, setModel] = useState(null);
  const [showMethod, setShowMethod] = useState(false);
  const [drawer, setDrawer] = useState(false); // control sheet, phones only

  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE,
      ...WHOLE_MUNICIPALITY,
    });
    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    mapRef.current = map;

    map.on("load", async () => {
      const data = await fetch(DATA_URL).then((r) => r.json());
      fetch(`/data/baiao_comparison.json${BUST}`)
        .then((r) => (r.ok ? r.json() : null))
        .then(setComparison)
        .catch(() => {});
      setSentinelDate(data.properties?.sentinel_date ?? null);
      setSources(data.properties?.sources ?? []);
      setModel(data.properties?.model ?? null);
      setDryness({ index: data.properties?.dryness_index, pctDry: data.properties?.pct_dry });
      const series = data.properties?.dryness_series ?? [];
      setDrynessSeries(series);
      setDrynessMonth(series.length - 1);

      map.addSource("zones", { type: "geojson", data });
      map.addLayer({
        id: "zones-fill",
        type: "fill",
        source: "zones",
        paint: { "fill-color": priorityColor(5), "fill-opacity": 0.55 },
      });
      map.addLayer({
        id: "zones-top",
        type: "line",
        source: "zones",
        filter: [">=", ["get", "pct"], 95],
        paint: { "line-color": "#ffffff", "line-width": 1.2, "line-opacity": 0.85 },
      });

      map.on("click", "zones-fill", (e) => {
        const f = e.features[0];
        setSelected({ ...f.properties, center: centroid(f) });
      });
      map.on("mouseenter", "zones-fill", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "zones-fill", () => (map.getCanvas().style.cursor = ""));

      addContextLayers(map);
    });

    return () => map.remove();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer || !map.getLayer("zones-fill")) return;
    // in the split view the LEFT pane is always our model; the right pane
    // (map2) is the one that carries the official hazard colours
    let color =
      view === "priority" ? priorityColor(alert)
      : view === "oficial" ? SUSC_COLOR
      : VIEWS[view].color;
    if (view === "secura" && drynessSeries.length && drynessMonth >= 0) {
      color = drynessMode === "anomaly"
        ? anomColor(`anom_m${drynessMonth}`)
        : drynessColorExpr(`ndmi_m${drynessMonth}`);
    }
    map.setPaintProperty("zones-fill", "fill-color", color);
    map.setPaintProperty("zones-fill", "fill-opacity", VIEWS[view].opacity);
    if (map.getLayer("zones-top")) {
      // white outline marks the CURRENT "Muito alta" class — follows the slider
      map.setFilter("zones-top", [">=", ["get", "pct"], 100 - alert]);
      map.setLayoutProperty("zones-top", "visibility", VIEWS[view].showTop ? "visible" : "none");
    }
  }, [view, alert, drynessMonth, drynessSeries, drynessMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer || !map.getLayer("ovl-fogos")) return;
    const yr = view === "susceptibility" ? fireYear : view === "oficial" ? anoComp : "";
    map.setFilter("ovl-fogos", ["==", ["get", "ano"], yr ? Number(yr) : 0]);
    const m2 = map2Ref.current;
    if (m2?.getLayer?.("ovl-fogos")) {
      m2.setFilter("ovl-fogos", ["==", ["get", "ano"], yr ? Number(yr) : 0]);
    }
  }, [fireYear, view, anoComp]);

  // side-by-side: build the right pane (official hazard) the first time the
  // comparison view opens, then keep both cameras in sync
  useEffect(() => {
    if (view !== "oficial") return;
    const map = mapRef.current;
    if (!map) return;

    if (!map2Ref.current) {
      const m2 = new maplibregl.Map({
        container: container2Ref.current,
        style: STYLE,
        center: map.getCenter(),
        zoom: map.getZoom(),
        attributionControl: false,
      });
      map2Ref.current = m2;
      m2.on("load", async () => {
        const data = await fetch(DATA_URL).then((r) => r.json());
        m2.addSource("zones", { type: "geojson", data });
        m2.addLayer({
          id: "zones-fill", type: "fill", source: "zones",
          paint: { "fill-color": OFFICIAL_COLOR, "fill-opacity": 0.55 },
        });
        addContextLayers(m2);
        quietly(() => m2.jumpTo(cameraOf(map))); // the style load may have nudged it
        setMap2Ready((v) => v + 1); // re-apply overlay visibility to the new pane
      });

      // Each direction guards on the OTHER direction's flag. A single shared
      // flag broke when the two maps animated at once (scroll zoom is animated),
      // leaving one pane behind until a page reload.
      let aMove = false, bMove = false;
      map.on("move", () => {
        if (bMove || quiet.current) return;
        aMove = true;
        m2.jumpTo(cameraOf(map));
        aMove = false;
      });
      m2.on("move", () => {
        if (aMove || quiet.current) return;
        bMove = true;
        map.jumpTo(cameraOf(m2));
        bMove = false;
      });
    }

    // The panes change size when the split layout opens or closes. The main map
    // is the source of truth here: the second pane may have been sitting on a
    // stale camera since the last visit, and resizing it emits a move that would
    // otherwise drag the main map back to that stale position.
    const t = setTimeout(() => {
      quietly(() => {
        map.resize();
        map2Ref.current?.resize();
        map2Ref.current?.jumpTo(cameraOf(map));
      });
    }, 60);
    return () => clearTimeout(t);
  }, [view]);

  // A hidden pane has zero size, so its map goes stale until something tells it
  // to measure again. Observing the containers covers every case, including the
  // window being resized while the comparison view is open.
  useEffect(() => {
    const obs = new ResizeObserver(() => {
      quietly(() => {
        mapRef.current?.resize();
        map2Ref.current?.resize();
      });
    });
    if (containerRef.current) obs.observe(containerRef.current);
    if (container2Ref.current) obs.observe(container2Ref.current);
    return () => obs.disconnect();
  }, []);

  // Each view is read from scratch: the open cell card belongs to the view it was
  // opened from, and the camera returns to the whole municipality so a zoomed-in
  // detail from one map is never carried over into another one.
  useEffect(() => {
    setSelected(null);
    setDrawer(false); // on a phone, picking a view means you want to see the map
    mapRef.current?.jumpTo(WHOLE_MUNICIPALITY);
    map2Ref.current?.jumpTo(WHOLE_MUNICIPALITY);
  }, [view]);

  // main map must resize when leaving the split layout too
  useEffect(() => {
    const t = setTimeout(() => mapRef.current?.resize(), 60);
    return () => clearTimeout(t);
  }, [view]);

  useEffect(() => {
    const vis = (on) => (on ? "visible" : "none");
    for (const map of [mapRef.current, map2Ref.current]) {
      if (!map?.getLayer?.("ovl-casas")) continue;
      map.setLayoutProperty("ovl-estradas", "visibility", vis(overlays.roads));
      map.setLayoutProperty("ovl-casas", "visibility", vis(overlays.buildings));
      map.setLayoutProperty("ovl-ignicoes", "visibility", vis(overlays.ignitions));
      for (const id of ["ovl-agua-fill", "ovl-agua-line", "ovl-agua-pt"]) {
        map.setLayoutProperty(id, "visibility", vis(overlays.water));
      }
    }
  }, [overlays, map2Ready]);


  // Opening the menu closes the cell card: the card describes a point on the map,
  // and leaving it behind the drawer would have it describe something you can no
  // longer see.
  const toggleDrawer = () => {
    setDrawer((v) => !v);
    setSelected(null);
  };

  return (
    <div className={`app${drawer ? " drawer-open" : ""}${selected ? " detail-open" : ""}`}>
      <button className="drawer-btn" onClick={toggleDrawer} aria-expanded={drawer}
              aria-label={drawer ? "Fechar menu" : "Abrir menu"}>
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"
             stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          {drawer ? <path d="M4 4l12 12M16 4L4 16" /> : <path d="M3 5h14M3 10h14M3 15h14" />}
        </svg>
      </button>
      {drawer && <div className="drawer-scrim" onClick={() => setDrawer(false)} />}

      <aside className="sidebar">
        <div className="brand">
          <span className="brand-eyebrow">Município de Baião</span>
          <h1>Prevenção de Incêndio Rural</h1>
          <p>
            Apoio à decisão na gestão de combustível: onde intervir primeiro,
            a partir do terreno, da vegetação, do histórico de incêndios e da
            exposição de habitações.
          </p>
        </div>

        <div className="sidebar-scroll">
        <div className="section">
          <h2>Ver no mapa</h2>
          <div className="toggle">
            <button className={view === "priority" ? "on" : ""} onClick={() => setView("priority")}>
              Onde atuar
            </button>
            <button className={view === "susceptibility" ? "on" : ""} onClick={() => setView("susceptibility")}>
              Onde arde
            </button>
            <button className={view === "fuel" ? "on" : ""} onClick={() => setView("fuel")}>
              Vegetação
            </button>
            <button className={view === "secura" ? "on" : ""} onClick={() => setView("secura")}>
              Secura
            </button>
            <button className={view === "oficial" ? "on" : ""} onClick={() => setView("oficial")}>
              Plano 2021
            </button>
          </div>
          <p className="hint">
            {view === "priority" && "Risco, valor exposto e dificuldade de combate: onde a prevenção protege mais."}
            {view === "susceptibility" && "Propensão estrutural: onde arde de forma recorrente, ao longo dos anos."}
            {view === "fuel" && "Tipo de vegetação e uso do solo (COS 2023)."}
            {view === "secura" && "Estado atual da vegetação medido por satélite, apenas sobre coberto vegetal. Descritivo, não é previsão."}
            {view === "oficial" && "Comparação lado a lado: model atualizado (esquerda) e cartografia oficial do PMDFCI 2021-2030 (direita)."}
          </p>

          {view === "oficial" && comparison && (
            <>
              <div className="fire-year">
                <label>Sobrepor os incêndios reais de:</label>
                <select value={anoComp} onChange={(e) => setAnoComp(e.target.value)}>
                  <option value="">Nenhum ano</option>
                  {comparison.anos.map((a) => (
                    <option key={a.ano} value={a.ano}>{a.ano}</option>
                  ))}
                </select>
              </div>
              <ComparisonTable dados={comparison} anoAtivo={anoComp} />
            </>
          )}

          {view === "oficial" && (
            <div className="docs">
              <a className="doc-btn" target="_blank" rel="noreferrer"
                 href="https://fires.icnf.pt/pmdfci/13_Porto/1302/3G/Caderno_II/PMDFCI_1302_Baiao_Caderno_II.pdf">
                Abrir o plano oficial (Caderno II)
              </a>
            </div>
          )}

          {view === "susceptibility" && (
            <div className="fire-year">
              <label>Sobrepor área ardida real (ICNF):</label>
              <select value={fireYear} onChange={(e) => setFireYear(e.target.value)}>
                <option value="">Nenhum ano</option>
                {Array.from({ length: 17 }, (_, i) => 2025 - i)
                  .filter((y) => y !== 2010)
                  .map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
              </select>
            </div>
          )}

          {view === "secura" && (() => {
            const cur = drynessSeries[drynessMonth];
            const idx = cur?.idx ?? dryness?.index;
            const pctDry = cur?.pct_dry ?? dryness?.pctDry;
            if (idx == null) return null;
            return (
              <>
                <div className="toggle" style={{ marginTop: 12 }}>
                  <button className={drynessMode === "atual" ? "on" : ""} onClick={() => setDrynessMode("atual")}>
                    Estado atual
                  </button>
                  <button className={drynessMode === "anomaly" ? "on" : ""} onClick={() => setDrynessMode("anomaly")}>
                    vs. o normal
                  </button>
                </div>

                {drynessMode === "atual" ? (
                  <div className="gauge">
                    <div className="gauge-num" style={{ color: drynessColor(idx) }}>
                      {idx}<span>/100</span>
                    </div>
                    <div className="gauge-label">
                      Secura em <strong>{cur?.mes ?? "atual"}</strong>: <strong>{drynessLabel(idx)}</strong><br />
                      {pctDry}% da vegetação dryness
                    </div>
                  </div>
                ) : (
                  <div className="gauge">
                    <div className="gauge-num" style={{ color: cur?.anom < 0 ? "#b3261e" : "#4fc3f7", fontSize: 26 }}>
                      {cur?.pct_acima_normal ?? "—"}<span>%</span>
                    </div>
                    <div className="gauge-label">
                      do concelho <strong>mais seco que o normal</strong> para {cur?.mes}<br />
                      comparado com os mesmos meses de 2015-2025
                    </div>
                  </div>
                )}

                {drynessSeries.length > 1 && (
                  <>
                    <div className="slider-row">
                      <label>
                        Mês: <strong>{drynessSeries[drynessMonth]?.mes}</strong> (arraste para ver a evolução)
                      </label>
                      <input
                        type="range" min="0" max={drynessSeries.length - 1} step="1"
                        value={drynessMonth}
                        onChange={(e) => setDrynessMonth(Number(e.target.value))}
                      />
                    </div>
                    <DrynessChart series={drynessSeries} active={drynessMonth} />
                  </>
                )}

                {cur?.imagens?.length > 0 && (
                  <div className="imgdates" key={`imgs-${drynessMonth}`}>
                    Imagens de satélite usadas em {cur.mes}:
                    <ul>
                      {/* same-day scenes (split granules) collapse into one line;
                          index in the key keeps React from reusing stale rows */}
                      {Object.entries(
                        cur.imagens.reduce((acc, im) => {
                          acc[im.data] = Math.min(acc[im.data] ?? 100, im.nuvem);
                          return acc;
                        }, {})
                      )
                        .sort()
                        .map(([data, nuvem], i) => (
                          <li key={`${drynessMonth}-${i}-${data}`}>
                            {data.split("-").reverse().join("/")}, {nuvem}% de nuvem
                          </li>
                        ))}
                    </ul>
                  </div>
                )}
              </>
            );
          })()}

          {view === "priority" && (
            <div className="slider-row">
              <label>
                Zonas em alert: <strong>top {alert}%</strong> do concelho
              </label>
              <input
                type="range" min="2" max="15" step="1" value={alert}
                onChange={(e) => setAlert(Number(e.target.value))}
              />
            </div>
          )}

          <div className="layers">
            {/* the dot carries the layer's colour on the map — identification,
                not decoration */}
            {[
              ["buildings", "Edifícios", "#ffd54f"],
              ["roads", "Estradas e caminhos", "#f5f5f5"],
              ["water", "Pontos de água", "#0277bd"],
              ["ignitions", "Ignições de 2026", "#ff6d00"],
            ].map(([key, label, color]) => (
              <label key={key} className="layer-check">
                <input
                  type="checkbox"
                  checked={overlays[key]}
                  onChange={(e) => setOverlays({ ...overlays, [key]: e.target.checked })}
                />
                <span className="layer-dot" style={{ background: color }} />
                {label}
              </label>
            ))}
          </div>
        </div>

        <div className="section">
          <h2>
            {view === "priority" && "Prioridade de intervenção"}
            {view === "susceptibility" && "Propensão estrutural"}
            {view === "fuel" && "Ocupação do solo"}
            {view === "secura" && (drynessMode === "anomaly" ? "Secura vs. o normal" : "Secura da vegetação")}
            {view === "oficial" && "Escala nos dois mapas"}
          </h2>
          {(view === "priority"
            ? [
                [`Muito alta (top ${alert}% do concelho)`, "#b3261e"],
                [`Alta (top ${2 * alert}%)`, "#e8710a"],
                [`Média (top ${4 * alert}%)`, "#f2c200"],
                ["Baixa", "#3a9d4e"],
              ]
            : view === "susceptibility"
            ? [
                ["Muito alta (top 5%)", "#b3261e"],
                ["Alta (top 15%)", "#e8710a"],
                ["Média (top 30%)", "#f2c200"],
                ["Baixa", "#3a9d4e"],
              ]
            : view === "oficial"
            ? [
                ["Muito alta (top 5%)", "#b3261e"],
                ["Alta (top 15%)", "#e8710a"],
                ["Média (top 30%)", "#f2c200"],
                ["Baixa", "#3a9d4e"],
              ]
            : view === "secura" && drynessMode === "anomaly"
            ? [
                ["Muito mais seco que o normal", "#b3261e"],
                ["Mais seco que o normal", "#e8710a"],
                ["Ligeiramente mais seco", "#f2c200"],
                ["Como o normal", "#e8eef4"],
                ["Menos seco que o normal", "#4fc3f7"],
              ]
            : view === "secura"
            ? [
                ["Muito dryness", "#b3261e"],
                ["Seca", "#e8710a"],
                ["Moderada", "#f2c200"],
                ["Húmida", "#3a9d4e"],
              ]
            : [
                ["Eucalipto", "#8e24aa"],
                ["Resinosas (pinheiro)", "#1b5e20"],
                ["Folhosas (carvalho…)", "#66bb6a"],
                ["Matos", "#9e9d24"],
                ["Agricultura", "#d7a45c"],
                ["Não combustível", "#9e9e9e"],
              ]
          ).map(([label, color]) => (
            <div className="legend-row" key={label}>
              <span className="swatch" style={{ background: color }} />
              {label}
            </div>
          ))}
        </div>

        <div className="section">
          <button className="method-btn" onClick={() => setShowMethod(true)}>
            Metodologia
            <span>Como é calculado o risco e a prioridade</span>
          </button>
        </div>

        {sources.length > 0 && (
          <div className="section">
            <details className="sources">
              <summary>Fontes de dados</summary>
              <ul>
                {sources.map((f) => (
                  <li key={f.o_que}>
                    <strong>{f.o_que}</strong>
                    <span>{f.quem}</span>
                    <em>{f.quando}</em>
                  </li>
                ))}
              </ul>
              <div className="sources-docs">
                Documentos do plano municipal (PMDFCI 2021-2030):
                <a target="_blank" rel="noreferrer"
                   href="https://fires.icnf.pt/pmdfci/13_Porto/1302/3G/Caderno_I/PMDFCI_1302_Baiao_Caderno_I.pdf">
                  Caderno I — Diagnóstico
                </a>
                <a target="_blank" rel="noreferrer"
                   href="https://fires.icnf.pt/pmdfci/13_Porto/1302/3G/Caderno_II/PMDFCI_1302_Baiao_Caderno_II.pdf">
                  Caderno II — Plano de Ação
                </a>
              </div>
            </details>
          </div>
        )}
        </div>
      </aside>

      <div className={`map${view === "oficial" ? " split" : ""}`}>
        <div className="pane">
          <div className="mapcanvas" ref={containerRef} />
          {view === "oficial" && <span className="pane-label">Modelo atualizado</span>}
        </div>
        <div className="pane pane2">
          <div className="mapcanvas" ref={container2Ref} />
          <span className="pane-label">Plano oficial 2021</span>
        </div>
        {selected && <Detail props={selected} alert={alert} onClose={() => setSelected(null)} />}
        {showMethod && (
          <MethodWindow model={model} comparison={comparison}
                       onClose={() => setShowMethod(false)} />
        )}
      </div>
    </div>
  );
}

function MethodWindow({ model, comparison, onClose }) {
  const inc = comparison?.incerteza;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="Fechar">×</button>
        <h2>Metodologia</h2>

        <h3>A que perguntas responde</h3>
        <ul className="modal-questions">
          <li><strong>Onde intervir primeiro?</strong> Ordena o território pelo
              efeito esperado da gestão de combustível, combinando a propensão
              para arder com o que está exposto e com a dificuldade de combate.</li>
          <li><strong>Que zones ardem de forma recorrente?</strong> Estima, para
              cada quadrícula, a tendência estrutural para ser percorrida pelo
              fogo ao longo dos anos.</li>
          <li><strong>Como está a vegetação agora?</strong> Mede o vigor e a
              humidade por satélite, e compara-os com o mesmo mês em anos
              anteriores.</li>
          <li><strong>Isto confirma o plano municipal?</strong> Confronta a
              estimativa com a cartografia oficial do PMDFCI e com os incêndios
              que ocorreram depois do plano.</li>
        </ul>

        <h3>O que se estima como risco</h3>
        <p>
          Para cada quadrícula, a propensão estrutural para arder: a tendência
          de um lugar para ser percorrido pelo fogo ao longo dos anos. Não é
          uma previsão para um dia nem para um incêndio concreto.
        </p>

        {model && (
          <>
            <h3>Variáveis usadas ({model.n_variaveis})</h3>
            {model.variaveis.map((g) => (
              <div className="var-group" key={g.grupo}>
                <span className="var-title">{g.grupo}</span>
                <ul>{g.itens.map((i) => <li key={i}>{i}</li>)}</ul>
              </div>
            ))}
            <h3>Duas camadas, variáveis diferentes</h3>
            <p>
              {model.excluidas?.length > 0 && (
                <>
                  {model.excluidas.join(" e ")} <strong>são usadas</strong>,
                  mas não no cálculo do risco. Entram na camada seguinte:
                </>
              )}
            </p>
            <table className="modal-layers">
              <tbody>
                <tr>
                  <th>Risco de arder</th>
                  <td>terreno, vegetação, presença humana e histórico de fogo
                      (as {model.n_variaveis} variáveis acima)</td>
                </tr>
                <tr>
                  <th>Prioridade de intervenção</th>
                  <td>risco × exposição (habitações próximas) ×{" "}
                      <strong>dificuldade de combate</strong> (distância a pontos
                      de água e a roads, e tempo de viagem dos bombeiros
                      calculado pela rede viária real)</td>
                </tr>
              </tbody>
            </table>
            <p className="modal-note">
              A separação foi verificada, não assumida: incluídas no risco, estas
              variáveis não acrescentavam capacidade de acertar, porque repetiam de
              forma menos direta o que o terreno já indicava. Não influenciam a
              probabilidade de um lugar arder; influenciam a rapidez com que o
              fogo é travado, e é aí que pesam.
            </p>

            <h3>Como foi treinado</h3>
            <p>
              Com o histórico do concelho entre {model.anos?.[0]} e {model.anos?.[1]}:
              cada uma das {model.n_celulas?.toLocaleString("pt-PT")} quadrículas
              observada em cada ano, num total de{" "}
              {model.n_linhas?.toLocaleString("pt-PT")} observações.
              Para cada ano, o cálculo usa apenas informação <strong>anterior</strong>,
              ou seja a vegetação e o histórico do ano precedente, de modo a que
              nunca esteja a olhar para o resultado que tenta estimar.
            </p>
            <p className="modal-note">
              Modelo em produção treinado a{" "}
              {model.treinado_em?.split("-").reverse().join("/")}, com registo
              dos parâmetros e dos dados usados, para que qualquer mapa publicado
              seja rastreável ao model que o produziu.
              <br /><br />
              <strong>Projeto em desenvolvimento.</strong> O model é
              reprocessado quando se verificam alterações relevantes nos dados
              de entrada: publicação de nova área ardida pelo ICNF, nova versão da
              carta de ocupação do solo, ou revisão das variáveis. A
              methodology continua sob avaliação e os resultados podem mudar
              entre versões.
            </p>
          </>
        )}

        <h3>Como foi verificado</h3>
        <p>
          Testou-se a estimar anos que o model nunca tinha visto, e também
          metade do concelho que nunca tinha visto, para confirmar que reconhece
          padrões em vez de memorizar lugares.
          {inc && (
            <>
              {" "}Comparado com a cartografia oficial do PMDFCI nos incêndios
              posteriores ao plano, os dois {inc.significativo ? "diferem de forma consistente" : "acertam de forma equivalente, dentro da margem de erro"}.
            </>
          )}
        </p>

        <h3>O que isto não faz</h3>
        <ul className="modal-limits">
          <li>Não prevê onde vai arder num dia concreto. Para isso seria preciso
              vento, temperatura e humidade do momento.</li>
          <li>Não sabe onde os bombeiros travaram fires que teriam alastrado:
              os dados mostram o que ardeu, não o que foi evitado.</li>
          <li>A ocupação do solo é de 2023, anterior aos incêndios de 2024.</li>
          <li>A resolução é de cerca de 29 metros: não distingue detalhes mais
              finos, como a limpeza de uma berma.</li>
          <li>O tempo indicado para os bombeiros é de viagem pela estrada, desde
              o quartel mais próximo. O tempo real até à primeira intervenção é
              maior, porque inclui a mobilização dos operacionais.</li>
        </ul>
      </div>
    </div>
  );
}

function ComparisonTable({ dados, anoAtivo }) {
  const { anos, media: m, incerteza: inc } = dados;
  if (!m) return null;
  return (
    <div className="comp">
      <div className="comp-head">
        Quem previu melhor os incêndios <strong>depois</strong> do plano?
      </div>
      <table className="comp-table">
        <thead>
          <tr><th>Ano</th><th>Ardeu</th><th>Modelo</th><th>Plano</th></tr>
        </thead>
        <tbody>
          {anos.map((a) => {
            const nosso = a.nosso > a.plano;
            return (
              <tr key={a.ano} className={String(a.ano) === String(anoAtivo) ? "on" : ""}>
                <td>{a.ano}</td>
                <td className="muted">{a.ardeu_pct}%</td>
                <td className={nosso ? "win" : ""}>{a.nosso.toFixed(3)}</td>
                <td className={!nosso ? "win" : ""}>{a.plano.toFixed(3)}</td>
              </tr>
            );
          })}
          <tr className="total">
            <td colSpan={2}>Média</td>
            <td className={m.nosso > m.plano ? "win" : ""}>{m.nosso.toFixed(3)}</td>
            <td className={m.plano > m.nosso ? "win" : ""}>{m.plano.toFixed(3)}</td>
          </tr>
        </tbody>
      </table>
      <p className="comp-note">
        Quanto mais alto o valor, mais vezes acertou onde viria a arder.
        {inc && !inc.significativo && (
          <> Os dois <strong>acertam de forma semelhante</strong>: a diferença
          entre eles cabe dentro da margem de erro.</>
        )}
        {inc && inc.significativo && (
          <> A <strong>{inc.dif_media > 0 ? "cartografia oficial" : "modelo"}</strong> acerta
          mais vezes, de forma consistente.</>
        )}{" "}
        A diferença está noutro lado: o model é recalculado todos os anos,
        enquanto a cartografia oficial se mantém fixa até 2030.

        <span className="info" tabIndex={0}>
          detalhe técnico
          <span className="tip">
            Medida: AUC (0,5 = acaso · 1,0 = previsão perfeita). Ambas as
            superfícies usaram apenas informação até {dados.plan_year} e foram
            avaliadas contra os incêndios de{" "}
            {dados.anos[0]?.ano}–{dados.anos[dados.anos.length - 1]?.ano}.
            {inc && (
              <>
                {" "}Média: {m.plano.toFixed(3)} (cartografia) vs {m.nosso.toFixed(3)} (model).
                Diferença de {inc.dif_media > 0 ? "+" : ""}{inc.dif_media.toFixed(3)},
                intervalo de confiança a 95% [{inc.ic95[0].toFixed(3)}, {inc.ic95[1].toFixed(3)}],
                obtido por bootstrap sobre blocos espaciais
                {!inc.significativo && ", que atravessa o zero, pelo que a diferença não é significativa"}.
              </>
            )}
          </span>
        </span>
      </p>
    </div>
  );
}

function DrynessChart({ series, active }) {
  const W = 300, H = 92, PAD = 22;
  const pts = series.map((s, i) => [
    PAD + (i * (W - 2 * PAD)) / Math.max(1, series.length - 1),
    H - 18 - ((s.idx ?? 0) / 100) * (H - 34),
  ]);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%">
        <line x1={PAD} y1={H - 18} x2={W - PAD} y2={H - 18} stroke="var(--border)" />
        <path d={path} fill="none" stroke="#e8710a" strokeWidth="2.5" strokeLinejoin="round" />
        {pts.map((p, i) => (
          <g key={i}>
            <circle
              cx={p[0]} cy={p[1]} r={i === active ? 5 : 3.5}
              fill={drynessColor(series[i].idx)} stroke="#fff" strokeWidth={i === active ? 2 : 1}
            />
            <text x={p[0]} y={H - 5} textAnchor="middle" fontSize="10" fill="var(--muted)">
              {series[i].mes}
            </text>
            <text x={p[0]} y={p[1] - 9} textAnchor="middle" fontSize="10.5"
                  fill={i === active ? "var(--text)" : "var(--muted)"} fontWeight={i === active ? 700 : 400}>
              {series[i].idx}
            </text>
          </g>
        ))}
      </svg>
      <div className="chart-cap">Evolução da secura (0 = húmido, 100 = muito seco)</div>
    </div>
  );
}

function Detail({ props, alert, onClose }) {
  const lvl = priorityLevel(props, alert);
  const reasons = explain(props);
  return (
    <div className="detail">
      <button className="close" onClick={onClose}>×</button>
      <span className="badge" style={{ background: lvl.color }}>Prioridade {lvl.label}</span>
      <h3>Porque prevenir aqui?</h3>
      <ul>
        {reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ul>
      <div className="factgrid">
        <div className="fact"><span>Vegetação</span><strong>{props.fuel}</strong></div>
        <div className="fact"><span>Ardeu (2009-25)</span><strong>{props.vezes_ardeu != null ? `${props.vezes_ardeu}×` : "—"}</strong></div>
        <div className="fact"><span>Inclinação</span><strong>{props.slope}°</strong></div>
        <div className="fact"><span>Casas (250 m)</span><strong>{props.houses_250m ?? "—"}</strong></div>
        <div className="fact"><span>Água a</span><strong>{props.agua_m != null ? `${props.agua_m} m` : "—"}</strong></div>
        <div className="fact"><span>Estrada a</span><strong>{props.estrada_m != null ? `${props.estrada_m} m` : "—"}</strong></div>
        <div className="fact"><span>Bombeiros a</span><strong>{props.bombeiros_min != null ? `${props.bombeiros_min} min` : "—"}</strong></div>
      </div>
    </div>
  );
}
