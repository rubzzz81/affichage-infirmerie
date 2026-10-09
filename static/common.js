// Définitions partagées entre l'écran d'affichage et la page de commande.
const STATUS = {
  present: { label: "Présente",              short: "Présente",     color: "#1e9e57", icon: '<path d="M5 13l4 4L19 7"/>' },
  consult: { label: "En consultation",       short: "Consultation", color: "#2563eb", icon: '<path d="M3 12h4l2-6 4 12 2-6h6"/>' },
  pause:   { label: "Absente temporairement", short: "Absente un instant", color: "#f08a00", icon: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>' },
  absent:  { label: "Absente",               short: "Absente",      color: "#d6232f", icon: '<path d="M6 6l12 12M18 6L6 18"/>' },
};

// "08:00" -> "8h", "08:30" -> "8h30"
function fmtHour(t) {
  if (!t) return "";
  const [h, m] = t.split(":");
  return `${parseInt(h, 10)}h${m === "00" ? "" : m}`;
}

function icon(status) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${STATUS[status].icon}</svg>`;
}

// Texte de retour prévu pour une absence temporaire.
function backText(back) {
  if (!back) return "";
  const now = new Date();
  const [h, m] = back.split(":").map(Number);
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m);
  return target > now ? `Retour vers ${fmtHour(back)}` : "Retour imminent";
}

// Connexion temps réel avec reprise automatique + filet de sécurité par interrogation.
function watchState(onState) {
  const pull = () => fetch("/api/state").then(r => r.json()).then(onState).catch(() => {});
  try {
    const es = new EventSource("/api/events");
    es.onmessage = e => onState(JSON.parse(e.data));
  } catch (e) {}
  setInterval(pull, 30000);
  pull();
}
