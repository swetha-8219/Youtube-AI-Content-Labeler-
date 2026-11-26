// Content script: finds the video, reads YouTube's own AI label,
// asks the backend for other signals, and draws the badge on the player.
(function () {
  const { computeVerdict } = window.AILabel;
  const BADGE_ID = "ai-label-layer-badge";
  let currentId = null;

  function send(msg) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage(msg, r => resolve(r || { ok: false, error: chrome.runtime.lastError?.message }));
      } catch (e) {
        resolve({ ok: false, error: String(e) }); // extension reloaded while the page stayed open
      }
    });
  }

  // Backend -> verdict inputs. Every check that didn't run stays null ("Not checked").
  async function fetchSignals(videoId) {
    const r = await send({ type: "verify", videoId });
    if (!r.ok) return { offline: true };
    const s = r.body.signals || {};
    return {
      youtubeApi: s.youtubeApi?.value ?? null,
      c2paAI: null, // needs the video file; YouTube links can't be checked (see README)
      synthid: s.synthid?.value ?? null,
      classifier: s.classifier?.value ?? null,
      crowdReports: s.crowd?.value ?? null
    };
  }

  async function reportAI(videoId) {
    const r = await send({ type: "report", videoId });
    if (r.status === 429) return "rate_limited";
    return r.ok ? r.body.status : "error";
  }

  function getVideoId() {
    const u = new URL(location.href);
    if (u.pathname === "/watch") return u.searchParams.get("v");
    const m = u.pathname.match(/^\/shorts\/([\w-]{6,})/);
    return m ? m[1] : null;
  }

  // YouTube shows AI disclosures as text near the player / in the description.
  // Match the known phrasings rather than brittle class names.
  // Kept strict on purpose: a description that merely talks ABOUT AI must not trigger it.
  const LABEL_PHRASE = /altered or synthetic content/i;
  function hasYouTubeAILabel() {
    // 1) The dedicated "How this content was made" disclosure section.
    const section = document.querySelector("ytd-how-this-was-made-section-view-model");
    if (section && LABEL_PHRASE.test(section.innerText || "")) return true;
    // 2) YouTube's own badge chips near the player / on Shorts overlays.
    // SAFETY: never read titles, channel names or descriptions; creators control those,
    // so "AI generated" in a title must not produce a red badge.
    const chips = document.querySelectorAll(
      "ytd-watch-metadata badge-shape, ytd-reel-player-overlay-renderer badge-shape"
    );
    for (const c of chips) {
      const t = (c.innerText || "").trim();
      if (t.length < 60 && (LABEL_PHRASE.test(t) || /^AI[- ]generated$|^Made with AI$/i.test(t))) return true;
    }
    return false;
  }

  function getPlayer() {
    return document.querySelector("#movie_player") ||
           document.querySelector("ytd-reel-video-renderer[is-active] #player-container") ||
           document.querySelector(".html5-video-player");
  }

  function renderBadge(player, verdict, videoId) {
    let badge = document.getElementById(BADGE_ID);
    if (!badge) {
      badge = document.createElement("div");
      badge.id = BADGE_ID;
    }
    if (badge.parentElement !== player) player.appendChild(badge);
    badge.className = "ail-badge ail-" + verdict.level;
    badge.innerHTML = "";

    const pill = document.createElement("button");
    pill.className = "ail-pill";
    pill.textContent = verdict.label;
    pill.title = verdict.why;
    badge.appendChild(pill);

    const panel = document.createElement("div");
    panel.className = "ail-panel";
    panel.hidden = true;
    const h = document.createElement("div");
    h.className = "ail-why";
    h.textContent = verdict.why;
    panel.appendChild(h);
    const list = document.createElement("ul");
    for (const e of verdict.evidence) {
      const li = document.createElement("li");
      li.className = e.hit ? "ail-hit" : "";
      li.innerHTML = `<span></span><b></b>`;
      li.querySelector("span").textContent = e.name;
      li.querySelector("b").textContent = e.result;
      list.appendChild(li);
    }
    panel.appendChild(list);
    if (verdict.offline) {
      const note = document.createElement("div");
      note.className = "ail-mock";
      note.textContent = "Backend not reachable, so only the on-page YouTube label was checked. Set the backend URL in the extension's options.";
      panel.appendChild(note);
    }
    const rep = document.createElement("button");
    rep.className = "ail-report";
    rep.textContent = "I think this is AI";
    rep.onclick = async (ev) => {
      ev.stopPropagation();
      rep.disabled = true;
      const status = await reportAI(videoId);
      rep.textContent = {
        added: "Thanks, reported",
        duplicate: "Already reported",
        rate_limited: "Too many reports, try later"
      }[status] || "Couldn't send report";
      if (status === "error") rep.disabled = false;
    };
    // Lets viewers push back on a label. Feeds the override-rate metric and,
    // after review, becomes a permanent eval case.
    const wrong = document.createElement("button");
    wrong.className = "ail-report ail-wrong";
    wrong.textContent = "This label looks wrong";
    wrong.onclick = async (ev) => {
      ev.stopPropagation();
      wrong.disabled = true;
      const r = await send({ type: "feedback", videoId, level: verdict.level });
      wrong.textContent = r.ok ? (r.body.status === "duplicate" ? "Already sent" : "Thanks, we'll review it")
        : r.status === 429 ? "Too many reports, try later" : "Couldn't send";
      if (!r.ok && r.status !== 429) wrong.disabled = false;
    };
    const row = document.createElement("div");
    row.className = "ail-actions";
    row.append(rep, wrong);
    panel.appendChild(row);
    badge.appendChild(panel);

    pill.onclick = (ev) => { ev.stopPropagation(); panel.hidden = !panel.hidden; };
  }

  async function update() {
    const id = getVideoId();
    const player = getPlayer();
    if (!id || !player) return;
    if (id === currentId && document.getElementById(BADGE_ID)) return;
    currentId = id;

    // 1) Instant verdict from the page alone.
    const local = { youtubeLabel: hasYouTubeAILabel() };
    renderBadge(player, computeVerdict(local), id);

    // 2) Enrich with backend signals.
    const remote = await fetchSignals(id);
    if (id !== currentId) return; // user navigated away
    const merged = { ...remote, youtubeLabel: local.youtubeLabel || hasYouTubeAILabel() };
    delete merged.offline;
    renderBadge(player, { ...computeVerdict(merged), offline: !!remote.offline }, id);
  }

  // YouTube is a single-page app: re-run on its navigation event, and on DOM changes as a fallback.
  window.addEventListener("yt-navigate-finish", () => { currentId = null; setTimeout(update, 600); });
  let t;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(update, 800); })
    .observe(document.body, { childList: true, subtree: true });
  update();
})();
