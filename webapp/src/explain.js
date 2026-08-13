// Plain-language explanation of a cell — deliberately NO ML jargon.
// Turns the model output into words a municipal technician says in a meeting.
//
// The sentences are written twice rather than assembled from translated
// fragments: word order, agreement and the natural phrasing differ enough
// between the two languages that stitching pieces produces stilted text.

import { isEN, t, fuelLabel } from "./i18n.js";

export function priorityLevel(props, alert = 5) {
  const pct = props.pct ?? 0;
  if (pct >= 100 - alert) return { label: t("Muito alta"), color: "#b3261e" };
  if (pct >= 100 - 2 * alert) return { label: t("Alta"), color: "#e8710a" };
  if (pct >= 100 - 4 * alert) return { label: t("Média"), color: "#f2c200" };
  return { label: t("Baixa"), color: "#3a9d4e" };
}

export function explain(props) {
  const r = [];
  const fuel = (props.fuel || "").toLowerCase();
  const km = (m) => (m / 1000).toFixed(1);

  if (isEN) {
    if (fuel.includes("mato")) r.push("it is brush, which catches and spreads easily");
    else if (fuel.includes("floresta")) r.push("it is dense forest, with fuel in abundance");
    else if (fuel.includes("agricult")) r.push("it is farmland");
    else if (fuel) r.push(`it is ${fuelLabel(props.fuel).toLowerCase()}`);

    if (props.vezes_ardeu >= 3) r.push(`it has burned repeatedly, ${props.vezes_ardeu} times on average since 2009`);
    else if (props.vezes_ardeu >= 1) r.push(`it has burned ${props.vezes_ardeu >= 2 ? `${props.vezes_ardeu} times ` : ""}since 2009`);
    else if (props.ardeu_antes_pct >= 50) r.push("part of this area has burned before");

    if (props.slope >= 20) r.push(`it sits on a very steep slope (${props.slope}°), where fire climbs fast`);
    else if (props.slope >= 12) r.push(`it sits on a slope (${props.slope}°)`);

    if (props.dist_casas <= 100) r.push(`it falls inside the legal fuel-management band around homes, with houses ${props.dist_casas} m away`);
    if (props.houses_250m >= 20) r.push(`there is a cluster of ${props.houses_250m} homes within 250 m`);
    else if (props.dist_casas > 100 && props.dist_casas <= 300) r.push(`there are homes nearby (${props.dist_casas} m)`);

    if (props.agua_m >= 1500) r.push(`the nearest water for refilling is ${km(props.agua_m)} km away, which hampers suppression and makes prevention matter more`);
    if (props.estrada_m >= 300) r.push(`access is difficult (road ${props.estrada_m} m away)`);
    if (props.bombeiros_min >= 15) r.push(`the nearest fire station is about ${props.bombeiros_min} minutes away by road, so the fire gains ground before crews arrive`);
    return r;
  }

  if (fuel.includes("mato")) r.push("é mato, que arranca e alastra com facilidade");
  else if (fuel.includes("floresta")) r.push("é floresta densa, combustível abundante");
  else if (fuel.includes("agricult")) r.push("é zona agrícola");
  else if (fuel) r.push(`é ${fuel}`);

  if (props.vezes_ardeu >= 3) r.push(`ardeu repetidamente, em média ${props.vezes_ardeu} vezes desde 2009`);
  else if (props.vezes_ardeu >= 1) r.push(`já ardeu ${props.vezes_ardeu >= 2 ? `${props.vezes_ardeu} vezes ` : ""}desde 2009`);
  else if (props.ardeu_antes_pct >= 50) r.push("parte desta área já ardeu no passado");

  if (props.slope >= 20) r.push(`está numa encosta muito inclinada (${props.slope}°), onde o fogo sobe depressa`);
  else if (props.slope >= 12) r.push(`está em encosta (${props.slope}°)`);

  if (props.dist_casas <= 100) r.push(`está dentro da faixa legal de gestão de combustível de habitações, com casas a ${props.dist_casas} m`);
  if (props.houses_250m >= 20) r.push(`há um aglomerado de ${props.houses_250m} casas a menos de 250 m`);
  else if (props.dist_casas > 100 && props.dist_casas <= 300) r.push(`há casas por perto (a ${props.dist_casas} m)`);

  if (props.agua_m >= 1500) r.push(`a água mais próxima para reabastecer fica a ${km(props.agua_m)} km, o que dificulta o combate e torna a prevenção mais importante`);
  if (props.estrada_m >= 300) r.push(`o acesso é difícil (estrada a ${props.estrada_m} m)`);
  if (props.bombeiros_min >= 15) r.push(`o quartel mais próximo fica a cerca de ${props.bombeiros_min} minutos de viagem, pelo que o fogo ganha avanço antes do combate`);
  return r;
}

export function consequenceText(props) {
  if (isEN) {
    if (props.houses_250m >= 20) return `Cluster of ${props.houses_250m} homes (250 m)`;
    if (props.dist_casas <= 100) return "Homes right beside it";
    if (props.dist_casas <= 250) return "Homes nearby";
    if (props.dist_casas <= 500) return "Some exposure to homes";
    if (props.agua_m >= 1500) return "Hard to fight (water far off)";
    return "Far from homes";
  }
  if (props.houses_250m >= 20) return `Aglomerado de ${props.houses_250m} casas (250 m)`;
  if (props.dist_casas <= 100) return "Casas mesmo ao lado";
  if (props.dist_casas <= 250) return "Habitações próximas";
  if (props.dist_casas <= 500) return "Alguma exposição a habitações";
  if (props.agua_m >= 1500) return "Difícil de combater (água longe)";
  return "Longe de habitações";
}
