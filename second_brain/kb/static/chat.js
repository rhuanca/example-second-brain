// Chat with your notes.
//
// Everything the model writes is untrusted text, and so are note titles, so this
// file never uses innerHTML: every node is built with createElement/textContent.
// A deliberately small Markdown subset is supported (paragraphs, lists, headings,
// bold, inline code, fenced code) plus [[note-id]] citations, which become links
// only when the id is one of the notes sent with that answer.
(() => {
  "use strict";

  const STORAGE_KEY = "kb-chat-v1";
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");
  const newChat = document.getElementById("new-chat");
  const welcome = document.getElementById("welcome");
  const NOTE_ID = /^[A-Za-z0-9._-]+$/;
  const ICON_PATHS = {
    youtube: "M4 7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3z M10 9.2v5.6l4.8-2.8z",
    article: "M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z M8 8h8 M8 12h8 M8 16h5",
  };

  // { role, content, source_ids, notes, cited, error }
  let history = load();
  let controller = null;

  // --- persistence (per tab; the page works without it) ---------------------
  function load() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(saved) ? saved : [];
    } catch (_) {
      return [];
    }
  }
  function save() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history));
    } catch (_) {
      /* storage unavailable: keep going in memory */
    }
  }

  // --- DOM helpers --------------------------------------------------------------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(kind) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICON_PATHS[kind] || ICON_PATHS.article);
    svg.appendChild(path);
    return svg;
  }

  function slotOf(note) {
    const slot = Number(note && note.slot);
    return Number.isInteger(slot) && slot >= 0 && slot <= 8 ? slot : 0;
  }

  function noteLink(id) {
    return "/notes/" + encodeURIComponent(id);
  }

  // --- rendering ----------------------------------------------------------------
  function renderInline(parent, text, notesById) {
    // **bold**, *italic* / _italic_, `code`, [[note-id]]
    const pattern = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\b_[^_]+_\b|`[^`]+`|\[\[[^\]]+\]\])/g;
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > last) parent.appendChild(document.createTextNode(text.slice(last, match.index)));
      const token = match[0];
      if (token.startsWith("**")) {
        parent.appendChild(el("strong", "", token.slice(2, -2)));
      } else if (token.startsWith("*") || token.startsWith("_")) {
        parent.appendChild(el("em", "", token.slice(1, -1)));
      } else if (token.startsWith("`")) {
        parent.appendChild(el("code", "", token.slice(1, -1)));
      } else {
        const id = token.slice(2, -2);
        const note = notesById.get(id);
        if (note && NOTE_ID.test(id)) {
          const a = el("a", "cite", note.title || id);
          a.href = noteLink(id);
          a.title = note.title || id;
          parent.appendChild(a);
        } else {
          parent.appendChild(document.createTextNode(token));
        }
      }
      last = match.index + token.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(container, text, notesById) {
    container.replaceChildren();
    const lines = text.replace(/\r\n/g, "\n").split("\n");
    let paragraph = [];
    let list = null;

    const flushParagraph = () => {
      if (!paragraph.length) return;
      const p = el("p");
      renderInline(p, paragraph.join(" "), notesById);
      container.appendChild(p);
      paragraph = [];
    };
    const closeList = () => { list = null; };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.trim().startsWith("```")) {
        flushParagraph(); closeList();
        const code = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
        container.appendChild(el("pre", "", code.join("\n")));
        continue;
      }
      const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (bullet) {
        flushParagraph();
        if (!list) {
          list = el(/^\s*\d/.test(line) ? "ol" : "ul");
          container.appendChild(list);
        }
        const li = el("li");
        renderInline(li, bullet[1], notesById);
        list.appendChild(li);
      } else if (heading) {
        flushParagraph(); closeList();
        const h = el("h4");
        renderInline(h, heading[1], notesById);
        container.appendChild(h);
      } else if (!line.trim()) {
        flushParagraph(); closeList();
      } else {
        closeList();
        paragraph.push(line.trim());
      }
    }
    flushParagraph();
  }

  function renderSources(container, notes, cited) {
    container.replaceChildren();
    if (!notes || !notes.length) return;
    const citedSet = new Set(cited || []);
    const ordered = [...notes].sort((a, b) => citedSet.has(b.id) - citedSet.has(a.id));
    const shown = citedSet.size ? ordered.filter((n) => citedSet.has(n.id)) : ordered;
    container.appendChild(el("p", "sources-label", citedSet.size ? "Sources" : "Notes consulted"));
    const grid = el("div", "sources");
    for (const note of shown) {
      if (!NOTE_ID.test(String(note.id))) continue;
      const card = el("a", "src-card" + (citedSet.size && !citedSet.has(note.id) ? " uncited" : ""));
      card.href = noteLink(note.id);
      const thumb = el("span", "thumb");
      if (typeof note.thumbnail === "string" && note.thumbnail.startsWith("https://i.ytimg.com/")) {
        const img = el("img");
        img.src = note.thumbnail;
        img.alt = "";
        img.loading = "lazy";
        thumb.appendChild(img);
      } else {
        const band = el("span", "band tint-s" + slotOf(note));
        band.appendChild(icon(note.source_type));
        thumb.appendChild(band);
      }
      card.appendChild(thumb);
      card.appendChild(el("span", "t", note.title || note.id));
      grid.appendChild(card);
    }
    container.appendChild(grid);
  }

  function renderTurn(turn) {
    if (turn.role === "user") {
      const bubble = el("div", "msg-user", turn.content);
      log.appendChild(bubble);
      return { bubble };
    }
    const wrap = el("div", "msg-assistant");
    const body = el("div", "body");
    const sources = el("div", "src-wrap");
    wrap.append(body, sources);
    log.appendChild(wrap);
    const view = { wrap, body, sources };
    updateAssistant(view, turn);
    return view;
  }

  function updateAssistant(view, turn) {
    const notesById = new Map((turn.notes || []).map((n) => [n.id, n]));
    if (turn.error) {
      view.body.replaceChildren();
      if (turn.content) renderMarkdown(view.body, turn.content, notesById);
      view.body.appendChild(el("div", "msg-error", turn.error));
    } else if (!turn.content) {
      view.body.replaceChildren(el("span", "thinking", "Searching your notes and thinking"));
    } else {
      renderMarkdown(view.body, turn.content, notesById);
      if (turn.truncated) view.body.appendChild(el("p", "msg-note", "(The answer was cut off.)"));
    }
    if (turn.done || turn.error) renderSources(view.sources, turn.notes, turn.cited);
  }

  function renderAll() {
    log.querySelectorAll(".msg-user, .msg-assistant").forEach((n) => n.remove());
    welcome.hidden = history.length > 0;
    newChat.hidden = history.length === 0;
    for (const turn of history) renderTurn(turn);
  }

  // --- talking to the server ----------------------------------------------------
  function setBusy(busy) {
    send.disabled = busy;
    stop.hidden = !busy;
    input.disabled = busy;
  }

  function payload() {
    // Failed answers are shown but never sent back to the model.
    return {
      messages: history
        .filter((t) => !t.error && t.content)
        .map((t) => ({ role: t.role, content: t.content, source_ids: t.cited && t.cited.length ? t.cited : (t.source_ids || []) })),
    };
  }

  async function ask(question) {
    question = question.trim();
    if (!question || controller) return;
    history.push({ role: "user", content: question });
    const turn = { role: "assistant", content: "", notes: [], cited: [], source_ids: [] };
    history.push(turn);
    welcome.hidden = true;
    newChat.hidden = false;
    renderTurn(history[history.length - 2]);
    const view = renderTurn(turn);
    view.wrap.scrollIntoView({ block: "end", behavior: "smooth" });
    setBusy(true);

    controller = new AbortController();
    let pending = false;
    const repaint = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; updateAssistant(view, turn); });
    };

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        let message = "Something went wrong (" + response.status + ").";
        try { message = (await response.json()).error || message; } catch (_) { /* not JSON */ }
        throw new Error(message);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === "sources") {
            turn.notes = Array.isArray(event.notes) ? event.notes : [];
            turn.source_ids = turn.notes.map((n) => n.id);
          } else if (event.type === "delta") {
            turn.content += String(event.text || "");
          } else if (event.type === "done") {
            turn.cited = Array.isArray(event.cited) ? event.cited : [];
            turn.truncated = Boolean(event.truncated);
            turn.done = true;
          } else if (event.type === "error") {
            turn.error = String(event.message || "Something went wrong.");
          }
          repaint();
        }
      }
      if (!turn.done && !turn.error) turn.error = "The answer stopped early.";
    } catch (err) {
      turn.error = err.name === "AbortError" ? "Stopped." : err.message || "Something went wrong.";
    } finally {
      controller = null;
      setBusy(false);
      updateAssistant(view, turn);
      save();
      input.focus();
    }
  }

  // --- wiring -------------------------------------------------------------------
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value;
    input.value = "";
    autosize();
    ask(question);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }
  input.addEventListener("input", autosize);
  stop.addEventListener("click", () => controller && controller.abort());
  newChat.addEventListener("click", () => {
    if (controller) controller.abort();
    history = [];
    save();
    renderAll();
    input.focus();
  });
  document.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => ask(button.dataset.prompt || ""));
  });

  renderAll();
})();
