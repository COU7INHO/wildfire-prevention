// Plain-language explanation of a zone — deliberately NO ML jargon.
// Turns the model output into words a municipal technician says in a meeting.

export function priorityLevel(props, alert = 5) {
  const pct = props.pct ?? 0;
  if (pct >= 100 - alert) return { label: "Muito alta", color: "#b3261e" };
  if (pct >= 100 - 2 * alert) return { label: "Alta", color: "#e8710a" };
  if (pct >= 100 - 4 * alert) return { label: "Média", color: "#f2c200" };
  return { label: "Baixa", color: "#3a9d4e" };
}

// Build the "why we should prevent here" sentence from the zone facts.
export function explain(props) {
  const reasons = [];

  const fuel = (props.fuel || "").toLowerCase();
  if (fuel.includes("mato")) reasons.push("é mato, que arranca e alastra com facilidade");
  else if (fuel.includes("floresta")) reasons.push("é floresta densa, combustível abundante");
  else if (fuel.includes("agricult")) reasons.push("é zona agrícola");
  else if (fuel) reasons.push(`é ${fuel}`);

  if (props.vezes_ardeu >= 3) reasons.push(`ardeu repetidamente, em média ${props.vezes_ardeu} vezes desde 2009`);
  else if (props.vezes_ardeu >= 1) reasons.push(`já ardeu ${props.vezes_ardeu >= 2 ? props.vezes_ardeu + " vezes" : ""} desde 2009`.replace("  ", " "));
  else if (props.ardeu_antes_pct >= 50) reasons.push("parte desta área já ardeu no passado");

  if (props.slope >= 20) reasons.push(`está numa encosta muito inclinada (${props.slope}°), onde o fogo sobe depressa`);
  else if (props.slope >= 12) reasons.push(`está em encosta (${props.slope}°)`);

  if (props.dist_casas <= 100) reasons.push(`está dentro da faixa legal de gestão de combustível de habitações, com casas a ${props.dist_casas} m`);
  if (props.houses_250m >= 20) reasons.push(`há um aglomerado de ${props.houses_250m} casas a menos de 250 m`);
  else if (props.dist_casas > 100 && props.dist_casas <= 300) reasons.push(`há casas por perto (a ${props.dist_casas} m)`);

  if (props.agua_m >= 1500) reasons.push(`a água mais próxima para reabastecer fica a ${(props.agua_m / 1000).toFixed(1)} km, o que dificulta o combate e torna a prevenção mais importante`);
  if (props.estrada_m >= 300) reasons.push(`o acesso é difícil (estrada a ${props.estrada_m} m)`);
  if (props.bombeiros_min >= 15) reasons.push(`o quartel mais próximo fica a cerca de ${props.bombeiros_min} minutos de viagem, pelo que o fogo ganha avanço antes do combate`);

  return reasons;
}

export function consequenceText(props) {
  if (props.houses_250m >= 20) return `Aglomerado de ${props.houses_250m} casas (250 m)`;
  if (props.dist_casas <= 100) return "Casas mesmo ao lado";
  if (props.dist_casas <= 250) return "Habitações próximas";
  if (props.dist_casas <= 500) return "Alguma exposição a habitações";
  if (props.agua_m >= 1500) return "Difícil de combater (água longe)";
  return "Longe de habitações";
}
