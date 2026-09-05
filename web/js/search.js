export function normalizeText(value = "") {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("it-IT")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

export function buildSearchIndex(players) {
  return players.map((player) => ({
    player,
    name: normalizeText(player.name),
    lastName: normalizeText(player.last_name),
    aliases: (player.aliases || []).map(normalizeText),
    blob: normalizeText([player.name, player.team, ...(player.aliases || [])].join(" ")),
  }));
}

function scoreResult(entry, query, tokens) {
  if (entry.lastName === query) return 400;
  if (tokens.some((token) => entry.lastName.startsWith(token))) return 300;
  if (tokens.some((token) => entry.lastName.includes(token))) return 200;
  if (entry.aliases.some((alias) => tokens.every((token) => alias.includes(token)))) return 100;
  return 0;
}

export function searchPlayers(index, query) {
  const normalizedQuery = normalizeText(query);
  const tokens = normalizedQuery.split(" ").filter(Boolean);
  if (!tokens.length) return index.map((entry) => entry.player);

  return index
    .filter((entry) => tokens.every((token) => entry.blob.includes(token)))
    .map((entry) => ({ player: entry.player, score: scoreResult(entry, normalizedQuery, tokens) }))
    .sort((a, b) => b.score - a.score || a.player.name.localeCompare(b.player.name, "it"))
    .map(({ player }) => player);
}

function editDistance(left, right) {
  let previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let row = 1; row <= left.length; row += 1) {
    const current = [row];
    for (let column = 1; column <= right.length; column += 1) {
      current[column] = Math.min(
        current[column - 1] + 1,
        previous[column] + 1,
        previous[column - 1] + (left[row - 1] === right[column - 1] ? 0 : 1),
      );
    }
    previous = current;
  }
  return previous[right.length];
}

export function closestLastNames(index, query, limit = 3) {
  const tokens = normalizeText(query).split(" ").filter(Boolean);
  const target = tokens.at(-1);
  if (!target || target.length < 2) return [];

  const seen = new Set();
  return index
    .filter((entry) => entry.lastName && !seen.has(entry.lastName) && seen.add(entry.lastName))
    .map((entry) => ({
      name: entry.player.name,
      distance: editDistance(target, entry.lastName),
    }))
    .sort((a, b) => a.distance - b.distance || a.name.localeCompare(b.name, "it"))
    .slice(0, limit)
    .filter(({ distance }) => distance <= Math.max(2, Math.floor(target.length / 2)));
}
