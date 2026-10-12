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
  if (!location.search.includes("nosse")) try {   // ?nosse : sans temps réel (captures d'écran)
    const es = new EventSource("/api/events");
    es.onmessage = e => onState(JSON.parse(e.data));
  } catch (e) {}
  setInterval(pull, 30000);
  pull();
}

// ---------------------------------------------------------------- Connexion (codes personnels)
// Utilisé par les pages /admin (infirmières) et /stats (administration, ou infirmière pour ses chiffres).
const Auth = {
  async me() {
    try { const r = await fetch("/api/me", { cache: "no-store" }); return r.ok ? await r.json() : null; } catch (e) { return null; }
  },
  async post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return { status: r.status, json: await r.json().catch(() => ({})) };
  },
  async logout() { await Auth.post("/api/logout"); location.reload(); },

  // Affiche l'écran de connexion dans `box` ; la promesse est résolue avec le compte connecté.
  async login(box, { tab = "nurse", nurse = "" } = {}) {
    let acc = { nurses: [], admins: [], hidden: false, totp: false };
    try { acc = Object.assign(acc, await (await fetch("/api/accounts", { cache: "no-store" })).json()); } catch (e) {}
    // Depuis Internet, le serveur ne donne aucun nom de compte : on saisit son identifiant (acc.hidden).
    const hasAdmin = acc.hidden ? acc.hasAdmins !== false : acc.admins.length > 0;
    const st = { tab: tab === "admin" && !hasAdmin ? "nurse" : tab, ident: nurse || "",
                 nurse: acc.nurses.some(n => n.id === nurse) ? nurse : "", admin: (acc.admins[0] || {}).id || "" };
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    return new Promise(resolve => {
      const draw = (msg = "") => {
        const list = st.tab === "nurse" ? acc.nurses : acc.admins, sel = st.tab === "nurse" ? st.nurse : st.admin;
        box.innerHTML = `
          <form class="lg" autocomplete="on">
            <h2>Connexion</h2>
            ${location.protocol !== "https:" ? '<p class="lg-warn">⚠ Connexion non chiffrée : ne saisissez pas votre code sur cette page. Demandez l\'accès en HTTPS.</p>' : ""}
            <div class="lg-seg" role="tablist">
              <button type="button" data-tab="nurse" class="${st.tab === "nurse" ? "on" : ""}">Infirmière</button>
              <button type="button" data-tab="admin" class="${st.tab === "admin" ? "on" : ""}" ${hasAdmin ? "" : "disabled"}>Administration</button>
            </div>
            ${acc.hidden
              ? `<label class="lg-lbl" for="lg-id">Identifiant</label>
                 <input id="lg-id" type="text" autocomplete="username" autocapitalize="off" spellcheck="false" maxlength="64" value="${esc(st.ident)}" required>`
              : list.length > 1 || st.tab === "nurse" ? `<p class="lg-lbl">${st.tab === "nurse" ? "Qui êtes-vous ?" : "Compte"}</p>
              <div class="lg-who">${list.map(a => `<button type="button" data-id="${esc(a.id)}" class="${a.id === sel ? "on" : ""}">${esc(a.name)}</button>`).join("")}</div>`
              : `<p class="lg-lbl">Compte : <b>${esc((list[0] || {}).name || "—")}</b></p>`}
            <label class="lg-lbl" for="lg-pin">Code personnel</label>
            <input id="lg-pin" type="password" autocomplete="current-password" autocapitalize="off" spellcheck="false" maxlength="64" required>
            ${acc.totp ? `<label class="lg-lbl" for="lg-otp">Code de l'application d'authentification</label>
            <input id="lg-otp" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="10" required>` : ""}
            <p class="lg-err" role="alert">${esc(msg)}</p>
            <button class="lg-go" type="submit">Se connecter</button>
          </form>`;
        const pin = box.querySelector("#lg-pin"), idBox = box.querySelector("#lg-id"), otp = box.querySelector("#lg-otp");
        if (acc.hidden ? st.ident : sel) pin.focus(); else if (idBox) idBox.focus();
        box.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { if (idBox) st.ident = idBox.value; st.tab = b.dataset.tab; draw(); });
        box.querySelectorAll(".lg-who [data-id]").forEach(b => b.onclick = () => { st[st.tab === "nurse" ? "nurse" : "admin"] = b.dataset.id; draw(); });
        box.querySelector("form").onsubmit = async e => {
          e.preventDefault();
          const id = acc.hidden ? idBox.value.trim() : (st.tab === "nurse" ? st.nurse : st.admin);
          if (!id) return draw(acc.hidden ? "Saisissez votre identifiant." : "Choisissez d'abord votre nom.");
          if (idBox) st.ident = id;
          const go = box.querySelector(".lg-go"); go.disabled = true; go.textContent = "Vérification…";
          const { status, json } = await Auth.post("/api/login", { role: st.tab, id, pin: pin.value, code: otp ? otp.value : undefined });
          if (status === 200 && json.ok) return resolve(await Auth.me());
          draw(json.error || (status === 403 ? "Connexion HTTPS requise." : "Connexion impossible."));
        };
      };
      draw();
    });
  },
};

(function () {   // style de l'écran de connexion (partagé)
  const css = `
  [hidden] { display: none !important; }
  .lg { background: #fff; border-radius: 20px; padding: 22px 18px; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 6px 20px -10px rgba(0,0,0,.12); max-width: 440px; margin: 8px auto; }
  .lg h2 { font-size: 22px; font-weight: 650; margin: 0 0 12px; }
  .lg-seg { display: grid; grid-template-columns: 1fr 1fr; background: #eef0f4; border-radius: 12px; padding: 4px; gap: 4px; margin-bottom: 14px; }
  .lg-seg button { appearance: none; border: 0; background: none; font: inherit; font-weight: 600; padding: 10px; border-radius: 9px; cursor: pointer; color: #6b7280; }
  .lg-seg button.on { background: #fff; color: #1b1f2a; box-shadow: 0 1px 3px rgba(0,0,0,.12); }
  .lg-seg button:disabled { opacity: .4; cursor: not-allowed; }
  .lg-lbl { display: block; font-size: 14px; color: #6b7280; margin: 8px 0 6px; }
  .lg-who { display: grid; gap: 8px; }
  .lg-who button { appearance: none; border: 1.5px solid #e3e6ec; background: #fff; border-radius: 14px; padding: 14px; font: inherit; font-size: 17px; font-weight: 600; color: #1b1f2a; cursor: pointer; text-align: left; }
  .lg-who button.on { border-color: #1b1f2a; background: #1b1f2a; color: #fff; }
  .lg input { width: 100%; font: inherit; font-size: 20px; letter-spacing: .15em; padding: 13px 14px; border: 1.5px solid #e3e6ec; border-radius: 14px; box-sizing: border-box; }
  .lg input:focus { outline: 2px solid #2563eb; border-color: transparent; }
  .lg #lg-id { letter-spacing: normal; font-size: 18px; }
  .lg-err { color: #b91c1c; font-size: 14px; min-height: 20px; margin: 8px 0 0; }
  .lg-warn { background: #fff4e0; color: #8a4b00; border-radius: 10px; padding: 10px 12px; font-size: 14px; margin: 0 0 12px; }
  .lg-go { appearance: none; border: 0; width: 100%; margin-top: 8px; padding: 15px; border-radius: 14px; background: #1e9e57; color: #fff; font: inherit; font-size: 17px; font-weight: 650; cursor: pointer; }
  .lg-go:disabled { opacity: .6; }`;
  const el = document.createElement("style");
  el.textContent = css;
  document.head.appendChild(el);
})();

// Adresses des autres sites (écran public, panneau, statistiques), SEULEMENT si l'on est arrivé par l'un de
// leurs noms. Arrivé par une adresse IP ou un autre nom (cas d'Internet), les liens restent relatifs :
// on ne renvoie jamais vers un nom que le visiteur ne sait pas résoudre (par exemple un « .local »).
function siteUrls(urls) {
  try {
    const hosts = Object.values(urls || {}).map(u => new URL(u).hostname);
    return hosts.includes(location.hostname) ? urls : {};
  } catch (e) { return {}; }
}
