// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// The web Components-V2 builder — a mountable widget over the pure model in
// cv2_model.js. Framework-free; a host page calls window.initCv2Builder(el, opts).
//
// WHY THIS IS NOT A PORT OF cv2_builder.py
// ----------------------------------------
// The in-Discord builder's interaction model is five workarounds for Discord's component
// budget, and a faithful port would import all five for problems a web page does not
// have:
//
//   can't click a rendered post -> a <select> listing "Text: Weekly res…" labels
//   no drag                     -> "Move up" / "Move down" buttons
//   5 components per row        -> "Open ▸" / "◂ Back" scope drilling, so you only ever
//                                  see ONE level of the tree at a time
//   no inline editing           -> a modal round-trip per field edit
//   no free-form layout         -> add-always-lands-after-the-selection
//
// Here instead:
//
//   1. THE CANVAS IS THE PREVIEW. Click a block in the rendered post to select it; the
//      picker <select> and its synthesized labels are gone, because you can see the
//      thing itself.
//   2. TEXT EDITS IN PLACE. Click text and type — raw markdown while focused, rendered
//      on blur. No modal.
//   3. DRAG REORDERS *AND* RE-PARENTS. This kills Move up/down AND Open/Back together:
//      the whole tree is on screen (depth is <= 3, so it always fits), which makes
//      "drag into that container" the same gesture as "drag one slot down".
//   4. RIGHT-CLICK ANYWHERE. On a block: edit / duplicate / wrap / add above / add below
//      / delete. On empty canvas: add a block. On touch, a long-press does the same —
//      there is no right mouse button on a phone.
//   5. INSERT WHERE YOU POINT. Hover a gap for a "+" rail; click it for a palette
//      filtered to what is legal *there*.
//
// TOUCH IS A FIRST-CLASS TARGET, not a smaller desktop. Dragging uses pointer events
// rather than HTML5 drag-and-drop, because dragstart/dragover/drop never fire on touch —
// so the entire rearranging model was dead on mobile. One pointer-event path now serves
// mouse, touch and pen. On narrow screens the layout changes shape too (palette as a
// strip, inspector as a bottom sheet); see the media queries in cv2_builder.css for why
// stacking the desktop columns was not enough.
//
// Plus three things Discord structurally cannot offer: undo/redo, validation anchored to
// the offending block (click a problem, it selects and scrolls), and nesting rules taught
// rather than hidden — a section's accessory is a visible slot, and an illegal drag says
// why instead of silently omitting the option.
//
// Rendering: the canvas is rendered client-side (cv2_model.renderMd) because it is the
// live editing surface — a server round-trip per keystroke is not an option. The
// authoritative render is cv2_html.render_cv2_nodes_html, shown in the publish
// confirmation, and the server re-sanitizes and re-validates on publish regardless. See
// the module docstring in dd/anchor/cv2_html.py.

(function () {
  "use strict";

  const M = window.CV2Model;

  // Palette entries. Accessory kinds are deliberately absent: they are reachable only
  // from a section's own slot, which is where the rule is legible.
  const PALETTE = [
    { kind: "text", label: "Text", glyph: "T", hint: "Markdown text — headings, bullets, links, emoji." },
    { kind: "container", label: "Container", glyph: "▤", hint: "A coloured card. Top level only." },
    { kind: "section", label: "Section", glyph: "◧", hint: "1–3 text blocks beside a thumbnail or button." },
    { kind: "media", label: "Image gallery", glyph: "▣", hint: "Up to 10 images in a grid." },
    { kind: "separator", label: "Separator", glyph: "—", hint: "A divider line or a gap." },
    { kind: "link_button", label: "Link button", glyph: "⬒", hint: "A row of link buttons." },
  ];

  const UNDO_LIMIT = 60;
  const AUTOSAVE_MS = 1200;

  const clone = (v) => JSON.parse(JSON.stringify(v));

  // --- motion ------------------------------------------------------------------------
  // Durations live in CSS (shared.css :root) so ONE prefers-reduced-motion rule reaches
  // the whole UI. WAAPI wants a number, so read the token back rather than duplicating
  // its value here — duplicate it and the two drift, and reduced motion silently stops
  // covering the JS-driven animations. These are local rather than in shared.js because
  // the builder is a standalone widget: cv2_builder.html does not load shared.js.
  const reduceMotion = () =>
    !!window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** A CSS duration token as milliseconds (`--dur` -> 140). 0 if it is missing. */
  function motionMs(token) {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue(token)
      .trim();
    const value = parseFloat(raw);
    if (!Number.isFinite(value)) return 0;
    return /ms$/.test(raw) ? value : value * 1000;
  }
  const EASE_OUT = "cubic-bezier(.2, 0, 0, 1)";
  const pk = (p) => JSON.stringify(p);
  const esc = (s) => M.esc(s);

  /**
   * Mount the builder.
   *
   * opts:
   *   nodes         initial node list (raw CV2 dicts)
   *   emoji         {name: url} map for :shortcode: preview substitution
   *   defaultAccent int seeded into a new container (cfg.embed_default_color)
   *   actionLabel   text for the publish button ("Post" / "Save" / "Send")
   *   onSave(nodes)          -> Promise, autosave
   *   onPublish(nodes)       -> Promise<{link}>, the real send/edit
   *   onPreview(nodes)       -> Promise<string>, server-rendered HTML for confirmation
   */
  function initCv2Builder(root, opts) {
    const options = opts || {};
    const state = {
      nodes: clone(options.nodes || []),
      sel: null,
      editing: null,
      editOrigin: "",
      undo: [],
      redo: [],
      problems: [],
      dirty: false,
      // The path of the block a mutation just produced, set for exactly one paint by
      // commit(). See renderBlock — this is what makes a per-block animation possible
      // without any node identity scheme.
      landed: null,
    };
    const emoji = options.emoji || null;
    const defaultAccent = options.defaultAccent;

    root.innerHTML = SHELL_HTML(options.actionLabel || "Post");
    const el = {
      palette: root.querySelector(".cv2b-palette"),
      canvas: root.querySelector(".cv2b-canvas"),
      inspector: root.querySelector(".cv2b-inspector"),
      undo: root.querySelector('[data-a="undo"]'),
      redo: root.querySelector('[data-a="redo"]'),
      add: root.querySelector('[data-a="add"]'),
      publish: root.querySelector('[data-a="publish"]'),
      status: root.querySelector(".cv2b-status"),
      count: root.querySelector(".cv2b-count"),
      toast: root.querySelector(".cv2b-toast"),
      dialog: root.querySelector(".cv2b-confirm"),
      confirmBody: root.querySelector(".cv2b-confirm-body"),
    };

    // ---- undo/redo -------------------------------------------------------------
    let liveKey = null; // coalesces a run of keystrokes in one inspector field

    function snapshot() {
      state.undo.push({ nodes: clone(state.nodes), sel: state.sel && state.sel.slice() });
      if (state.undo.length > UNDO_LIMIT) state.undo.shift();
      state.redo.length = 0;
    }
    function commit(label, fn) {
      snapshot();
      liveKey = null;
      fn();
      markDirty();
      // Every mutation returns the path it wants selected — an explicit contract in
      // cv2_model.js — so the block that just changed is already known, with no node
      // identity scheme and nothing for the full innerHTML rebuild to lose. Mark it for
      // exactly this paint, then drop the mark so the NEXT one (a keystroke, a plain
      // selection) does not replay the animation. Undo cannot desync it: it is recomputed
      // from state.sel on every commit rather than remembered.
      state.landed = state.sel;
      render();
      state.landed = null;
      setStatus(label);
    }

    /** Play a block's exit, then commit its removal.
     *
     *  The mirror of the landing mark, and it has to work the other way round: an
     *  addition can be marked when it arrives, but a deletion must animate BEFORE the
     *  mutation — afterwards there is no block left to animate. Awaiting the collapse
     *  means the rebuild lands on a canvas the block has already vacated, so the gap is
     *  closed rather than snapping shut behind it.
     *
     *  .cv2b-blk carries no margin or padding of its own (spacing lives in the rails), so
     *  animating height alone closes the space cleanly. */
    function commitAfterCollapse(path, label, fn) {
      const node = blockEl(path);
      if (!node || !node.animate || reduceMotion()) return commit(label, fn);
      const height = node.getBoundingClientRect().height;
      node.style.overflow = "hidden";
      // Commit on cancel too — an animation interrupted by another render must not
      // swallow the deletion and leave the block on screen.
      const done = () => commit(label, fn);
      node
        .animate(
          [
            { height: height + "px", opacity: 1 },
            { height: "0px", opacity: 0 },
          ],
          { duration: motionMs("--dur-fast"), easing: EASE_OUT },
        )
        .finished.then(done, done);
    }
    function undo() {
      if (!state.undo.length) return;
      state.redo.push({ nodes: clone(state.nodes), sel: state.sel });
      const prev = state.undo.pop();
      state.nodes = prev.nodes;
      state.sel = prev.sel;
      state.editing = null;
      liveKey = null;
      markDirty();
      render();
      setStatus("Undone");
    }
    function redo() {
      if (!state.redo.length) return;
      state.undo.push({ nodes: clone(state.nodes), sel: state.sel });
      const next = state.redo.pop();
      state.nodes = next.nodes;
      state.sel = next.sel;
      state.editing = null;
      liveKey = null;
      markDirty();
      render();
      setStatus("Redone");
    }

    // ---- autosave --------------------------------------------------------------
    let saveTimer = null;
    function markDirty() {
      state.dirty = true;
      if (!options.onSave) return;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(flush, AUTOSAVE_MS);
    }
    async function flush() {
      if (!options.onSave || !state.dirty) return;
      const payload = clone(state.nodes);
      try {
        await options.onSave(payload);
        state.dirty = false;
        setStatus("Saved");
      } catch (e) {
        setStatus("Couldn't save — retrying on the next change", true);
      }
    }
    // A drive-by close shouldn't drop the last edit.
    window.addEventListener("beforeunload", (e) => {
      if (!state.dirty) return;
      flush();
      e.preventDefault();
      e.returnValue = "";
    });

    // ---- render ----------------------------------------------------------------

    function problemPaths() {
      const set = new Set();
      state.problems.forEach((p) => p.path && set.add(pk(p.path)));
      return set;
    }

    function paintCanvas() {
      state.problems = M.validate(state.nodes);
      el.canvas.innerHTML = state.nodes.length
        ? renderScope([], state.nodes, problemPaths())
        : // The first thing an author with a blank draft sees, so it has to name a
          // gesture they actually have: there is no palette rail and no right-click on
          // a phone. Both phrasings ship and the media query picks one.
          '<div class="cv2b-empty">Nothing here yet.<br>' +
          '<span class="cv2b-wide-only">Drag a block from the left, or ' +
          "<b>right-click</b> for the menu.</span>" +
          // Not "press and hold" — the long-press menu hangs off a block, and there is
          // no block yet.
          '<span class="cv2b-narrow-only">Tap <b>+ Add</b> to start.</span></div>';
    }

    function render() {
      paintCanvas();
      renderInspector();
      el.undo.disabled = !state.undo.length;
      el.redo.disabled = !state.redo.length;
      // Show the text budget, not just block counts: the 4000-character cap is the
      // limit a real post actually hits, and it is invisible until Discord refuses the
      // send. Warn before the cap so there is room to react.
      const textLen = M.totalTextLength(state.nodes);
      el.count.textContent =
        countNodes(state.nodes) +
        " blocks · " +
        textLen +
        "/" +
        M.MAX_TEXT +
        " chars";
      el.count.classList.toggle("cv2b-count-warn", textLen > M.MAX_TEXT * 0.9);
      el.count.classList.toggle("cv2b-count-over", textLen > M.MAX_TEXT);
      el.count.title =
        state.nodes.length + " of " + M.MAX_TOP_LEVEL + " top-level blocks";
    }

    /** Repaint error outlines + the inspector without rebuilding the canvas DOM. */
    function refreshValidity() {
      state.problems = M.validate(state.nodes);
      const bad = problemPaths();
      el.canvas.querySelectorAll(".cv2b-blk").forEach((n) => {
        n.classList.toggle("cv2b-invalid", bad.has(n.dataset.path));
      });
      renderInspector();
      el.undo.disabled = !state.undo.length;
    }

    function countNodes(list) {
      return list.reduce(
        (n, x) => n + 1 + countNodes(x.components || []) + (x.accessory ? 1 : 0),
        0,
      );
    }

    function renderScope(scope, list, bad) {
      const out = ['<div class="cv2b-scope">', rail(scope, 0)];
      list.forEach((node, i) => {
        out.push(renderBlock(scope.concat([i]), node, bad));
        out.push(rail(scope, i + 1));
      });
      out.push("</div>");
      return out.join("");
    }

    function rail(scope, index) {
      return (
        '<div class="cv2b-rail" data-scope="' +
        esc(pk(scope)) +
        '" data-index="' +
        index +
        '"></div>'
      );
    }

    function renderBlock(path, node, bad) {
      const k = M.kind(node);
      const cls = ["cv2b-blk"];
      if (M.samePath(path, state.sel)) cls.push("cv2b-sel");
      if (bad.has(pk(path))) cls.push("cv2b-invalid");
      // Set by commit() for one paint only: the block this change produced.
      if (state.landed && M.samePath(path, state.landed)) cls.push("cv2b-landed");
      return (
        '<div class="' +
        cls.join(" ") +
        '" data-path="' +
        esc(pk(path)) +
        '" data-kind="' +
        k +
        '">' +
        '<span class="cv2b-grip" title="Drag to move">⠿</span>' +
        '<span class="cv2b-tag">' +
        esc(M.KIND_LABEL[k] || k) +
        "</span>" +
        '<div class="cv2b-body">' +
        renderBody(path, node, k, bad) +
        "</div></div>"
      );
    }

    function renderBody(path, node, k, bad) {
      switch (k) {
        case "container": {
          const accent = Number.isInteger(node.accent_color)
            ? ' style="border-left-color:#' +
              (node.accent_color & 0xffffff).toString(16).padStart(6, "0") +
              '"'
            : "";
          const inner = (node.components || []).length
            ? renderScope(path, node.components, bad)
            : '<div class="cv2-placeholder">Empty container — drop blocks in.</div>' +
              rail(path, 0);
          return '<div class="cv2-container"' + accent + ">" + inner + "</div>";
        }
        case "text": {
          if (M.samePath(path, state.editing)) {
            // A contenteditable, not a <textarea>: a textarea can only hold characters,
            // so emoji would drop back to raw `<:name:123>` the moment you started
            // editing — exactly the text you came to the builder to avoid reading.
            return (
              '<div class="cv2b-edit" data-editing="1" contenteditable="true" ' +
              'spellcheck="true" autocapitalize="sentences">' +
              editorHtml(node.content || "") +
              "</div>" +
              '<span class="cv2b-edit-hint">markdown · type : for emoji · Esc to finish</span>'
            );
          }
          const body = String(node.content || "").trim()
            ? M.renderMd(node.content, emoji)
            : '<span class="cv2-placeholder">Empty text — click to write.</span>';
          return '<div class="cv2-text">' + body + "</div>";
        }
        case "section":
          return (
            '<div class="cv2-section"><div class="cv2-section-body">' +
            renderScope(path, node.components || [], bad) +
            "</div>" +
            renderAccessory(path, node.accessory) +
            "</div>"
          );
        case "media": {
          const shown = (node.items || []).filter((i) => (i.media || {}).url);
          if (!shown.length) {
            return '<div class="cv2-placeholder">Image gallery — add URLs on the right.</div>';
          }
          const layout = { 1: "n1", 2: "n2", 3: "n3", 4: "n4" }[shown.length] || "many";
          return (
            '<div class="cv2-media ' +
            layout +
            '">' +
            shown
              .map(
                (it) =>
                  '<span class="cv2-media-item' +
                  (it.spoiler ? " cv2-spoiler" : "") +
                  '"><img src="' +
                  esc(it.media.url) +
                  '" alt="' +
                  esc(it.description || "") +
                  '" loading="lazy"></span>',
              )
              .join("") +
            "</div>"
          );
        }
        case "separator":
          return node.divider === false
            ? '<div class="cv2-spacer"></div>'
            : '<hr class="cv2-sep"' +
                (node.spacing === 2 ? ' style="margin:.5rem 0"' : "") +
                ">";
        case "link_button": {
          const btns = node.type === M.ACTION_ROW ? node.components || [] : [node];
          return (
            '<div class="cv2-buttons">' +
            btns
              .map(
                (b) =>
                  '<span class="cv2-button">' +
                  M.buttonEmojiHtml(b.emoji) +
                  esc(b.label || "(no label)") +
                  "</span>",
              )
              .join("") +
            "</div>"
          );
        }
        case "thumbnail": {
          const url = (node.media || {}).url;
          return url
            ? '<img class="cv2-thumb" src="' + esc(url) + '" alt="">'
            : '<div class="cv2b-acc-empty">no image URL</div>';
        }
        default:
          return (
            '<div class="cv2-placeholder">Unsupported component (type ' +
            esc(node.type) +
            ")</div>"
          );
      }
    }

    // A section's accessory is a real, visible slot. The in-Discord builder could only
    // express this rule as two "acc_*" options that appear in a dropdown once you have
    // drilled into a section — invisible until you already knew about it.
    function renderAccessory(sectionPath, acc) {
      const path = sectionPath.concat(["acc"]);
      if (!acc) {
        return (
          '<div class="cv2b-acc-slot" data-accslot="' +
          esc(pk(sectionPath)) +
          '">drop a thumbnail<br>or button</div>'
        );
      }
      const k = M.kind(acc);
      const sel = M.samePath(path, state.sel) ? " cv2b-sel" : "";
      const body =
        k === "thumbnail"
          ? (acc.media || {}).url
            ? '<img class="cv2-thumb" src="' + esc(acc.media.url) + '" alt="">'
            : '<div class="cv2b-acc-empty cv2b-acc-bad">image URL missing</div>'
          : '<span class="cv2-button">' +
            esc(M.buttonOf(acc).label || "(no label)") +
            "</span>";
      return (
        '<div class="cv2b-blk cv2b-acc-filled' +
        sel +
        '" data-path="' +
        esc(pk(path)) +
        '" data-kind="' +
        k +
        '"><span class="cv2b-tag">' +
        esc(M.KIND_LABEL[k]) +
        '</span><div class="cv2b-body">' +
        body +
        "</div></div>"
      );
    }

    // ---- inspector -------------------------------------------------------------

    function safeResolve(path) {
      try {
        return M.resolve(state.nodes, path);
      } catch (e) {
        state.sel = null;
        return null;
      }
    }

    // The properties sheet (mobile) must be dismissable and must not trap content behind
    // it. `sheetFor` tracks which block it is showing so dismissing one block's sheet
    // doesn't suppress the next one's.
    let sheetFor = null;
    let sheetDismissed = false;

    // Kinds whose properties can ONLY be reached through the sheet. Text is edited in
    // place on the canvas and a section has no fields of its own, so auto-opening for
    // those puts a second editor over the message for no gain — on a phone it covers the
    // thing you are editing with a duplicate of itself.
    const SHEET_KINDS = ["container", "media", "separator", "link_button", "thumbnail"];

    function sheetWorthOpening(node) {
      return !!node && SHEET_KINDS.indexOf(M.kind(node)) !== -1;
    }

    function syncSheet(hasNode) {
      const key = state.sel ? pk(state.sel) : null;
      if (key !== sheetFor) {
        sheetFor = key;
        sheetDismissed = false;
      }
      const open = hasNode && !sheetDismissed;
      el.inspector.classList.toggle("cv2b-sheet-open", open);
      // Reserve scroll room equal to the sheet's real height, so the bottom of the
      // message stays reachable instead of being permanently covered. Measured rather
      // than assumed, or a short sheet leaves a big dead gap.
      requestAnimationFrame(() => {
        const h = open ? el.inspector.getBoundingClientRect().height : 0;
        root.style.setProperty("--cv2b-sheet-h", (h ? Math.round(h) : 0) + "px");
      });
    }

    function renderInspector() {
      const node = state.sel ? safeResolve(state.sel) : null;
      // The close control lives in a fixed header, everything else in a scrolling body —
      // outside the scroll area, or on a long sheet (a gallery with ten URLs) the ✕
      // scrolls away exactly when you want it.
      //
      // There is deliberately no grab-bar pill. It looked like a drag handle, which on a
      // bottom sheet promises swipe-to-dismiss, but it only ever answered a tap. A
      // control that lies about its gesture is worse than one fewer control.
      const header =
        '<div class="cv2b-insp-bar">' +
        '<button type="button" class="cv2b-sheet-close" data-a="sheet-close" ' +
        'aria-label="Close properties" title="Close">✕</button>' +
        "</div>";
      const parts = [];
      if (!node) {
        parts.push(
          '<div class="cv2b-insp-empty"><div class="cv2b-insp-head"><h3>Nothing selected</h3></div>' +
            "<p>Click a block in the message to edit it.</p><ul>" +
            "<li><b>Click</b> text to type into it</li>" +
            "<li><b>Right-click</b> any block for its actions</li>" +
            "<li><b>Drag</b> the ⠿ handle to move or re-nest</li>" +
            "<li>Hover a gap for the <b>+</b> insert point</li>" +
            "<li><kbd>Ctrl/Cmd</kbd>+<kbd>Z</kbd> undoes anything</li></ul></div>",
        );
      } else {
        const k = M.kind(node);
        parts.push(
          '<div class="cv2b-insp-head"><h3>' +
            esc(M.KIND_LABEL[k] || k) +
            "</h3></div>" +
            fieldsFor(k, node),
        );
      }
      parts.push(
        state.problems.length
          ? '<div class="cv2b-problems"><h4>Not ready to post</h4>' +
              state.problems
                .map(
                  (p, i) =>
                    '<button type="button" data-problem="' +
                    i +
                    '">• ' +
                    esc(p.msg) +
                    "</button>",
                )
                .join("") +
              "</div>"
          : '<div class="cv2b-ok">✓ Ready to post.</div>',
      );
      el.inspector.innerHTML =
        header + '<div class="cv2b-insp-body">' + parts.join("") + "</div>";
      el.publish.disabled = state.problems.length > 0;
      // On a phone the inspector is a bottom sheet (see cv2_builder.css): open it only
      // when there is something to edit AND the author hasn't dismissed it. The class is
      // inert on desktop, where it is a static column.
      syncSheet(sheetWorthOpening(node));
    }

    function fieldsFor(k, node) {
      switch (k) {
        case "container": {
          const has = Number.isInteger(node.accent_color);
          const hex = has
            ? "#" + (node.accent_color & 0xffffff).toString(16).padStart(6, "0")
            : "#ec42a5";
          return (
            '<div class="cv2b-field"><label>Accent colour</label><div class="cv2b-row">' +
            '<input type="color" data-prop="accent" value="' +
            esc(hex) +
            '">' +
            '<input type="text" data-prop="accentHex" value="' +
            (has ? esc(hex) : "") +
            '" placeholder="none"></div>' +
            '<label class="cv2b-inline"><input type="checkbox" data-prop="accentOff"' +
            (has ? "" : " checked") +
            "> No accent bar</label></div>" +
            '<label class="cv2b-inline"><input type="checkbox" data-prop="spoiler"' +
            (node.spoiler ? " checked" : "") +
            "> Spoiler</label>"
          );
        }
        case "text":
          return (
            '<div class="cv2b-field"><label>Content</label>' +
            '<textarea data-prop="content" rows="8">' +
            esc(node.content || "") +
            "</textarea>" +
            '<span class="cv2b-help"><code># ## ###</code> headings · <code>-#</code> small · ' +
            "<code>- </code> bullet · <code>**bold**</code> · <code>[text](url)</code> · " +
            "<code>:emoji:</code></span></div>"
          );
        case "separator":
          return (
            '<label class="cv2b-inline"><input type="checkbox" data-prop="divider"' +
            (node.divider !== false ? " checked" : "") +
            "> Show a divider line</label>" +
            '<div class="cv2b-field"><label>Spacing</label><select data-prop="spacing">' +
            '<option value="1"' +
            (node.spacing !== 2 ? " selected" : "") +
            ">Small</option>" +
            '<option value="2"' +
            (node.spacing === 2 ? " selected" : "") +
            ">Large</option></select></div>"
          );
        case "media": {
          const items = node.items || [];
          const rows = items
            .map(
              (it, i) =>
                '<div class="cv2b-media-item">' +
                '<div class="cv2b-url-row"><input type="url" data-prop="mediaUrl" ' +
                'data-i="' +
                i +
                '" value="' +
                esc((it.media || {}).url || "") +
                '" placeholder="https://…">' +
                '<button type="button" class="cv2b-icon" data-act="mediaDel" data-i="' +
                i +
                '" title="Remove">✕</button></div>' +
                // Alt text is an accessibility field, not a nicety — an image-only post
                // is unreadable to a screen reader without it.
                '<input type="text" data-prop="mediaAlt" data-i="' +
                i +
                '" value="' +
                esc(it.description || "") +
                '" placeholder="Alt text (describes the image)">' +
                '<label class="cv2b-inline"><input type="checkbox" ' +
                'data-prop="mediaSpoiler" data-i="' +
                i +
                '"' +
                (it.spoiler ? " checked" : "") +
                "> Spoiler</label></div>",
            )
            .join("");
          return (
            '<div class="cv2b-field"><label>Images <span class="cv2b-help">(' +
            items.length +
            "/" +
            M.MAX_GALLERY_ITEMS +
            ')</span></label>' +
            (rows || '<span class="cv2b-help">No images yet.</span>') +
            '<button type="button" data-act="mediaAdd"' +
            (items.length >= M.MAX_GALLERY_ITEMS ? " disabled" : "") +
            ">Add image URL</button></div>"
          );
        }
        case "link_button": {
          // A row can hold up to five buttons. Editing only the first (which is what
          // buttonOf gives you) left every other button in a row visible but
          // uneditable — reachable only by deleting the row and rebuilding it.
          const btns = M.buttonsOf(node);
          const isRow = node.type === M.ACTION_ROW;
          const group = (b, i) =>
            (btns.length > 1
              ? '<div class="cv2b-btn-head"><span>Button ' +
                (i + 1) +
                "</span>" +
                '<button type="button" class="cv2b-icon" data-act="btnDel" data-i="' +
                i +
                '" title="Remove this button">✕</button></div>'
              : "") +
            '<div class="cv2b-field"><label>Label</label>' +
            '<input type="text" data-prop="btnLabel" data-i="' +
            i +
            '" value="' +
            esc(b.label || "") +
            '"></div>' +
            '<div class="cv2b-field"><label>URL</label>' +
            '<input type="url" data-prop="btnUrl" data-i="' +
            i +
            '" value="' +
            esc(b.url || "") +
            '" placeholder="https://…"></div>' +
            '<div class="cv2b-field"><label>Emoji <span class="cv2b-help">optional' +
            "</span></label>" +
            '<div class="cv2b-row"><input type="text" data-prop="btnEmoji" data-i="' +
            i +
            '" maxlength="32" value="' +
            esc((b.emoji || {}).name || "") +
            '" placeholder="name or 🙂">' +
            // Show what it resolved to: a server emoji renders as its image, so a name
            // that matched is visibly different from one that did not.
            '<span class="cv2b-emoji-preview">' +
            M.buttonEmojiHtml(b.emoji) +
            "</span></div>" +
            '<span class="cv2b-help">A server emoji\u2019s name, or a plain ' +
            "emoji character.</span></div>";
          return (
            '<div class="cv2b-btn-list">' +
            btns.map(group).join("") +
            "</div>" +
            // A section accessory is exactly one bare button, so it never grows a row.
            (isRow
              ? '<button type="button" data-act="btnAdd"' +
                (btns.length >= M.MAX_ROW_BUTTONS ? " disabled" : "") +
                ">Add another button</button>" +
                '<span class="cv2b-help">' +
                btns.length +
                "/" +
                M.MAX_ROW_BUTTONS +
                " in this row</span>"
              : "")
          );
        }
        case "thumbnail":
          return (
            '<div class="cv2b-field"><label>Image URL</label><input type="url" data-prop="thumbUrl" value="' +
            esc((node.media || {}).url || "") +
            '" placeholder="https://…"></div>' +
            '<div class="cv2b-field"><label>Alt text <span class="cv2b-help">optional</span></label>' +
            '<input type="text" data-prop="thumbDesc" value="' +
            esc(node.description || "") +
            '"></div>' +
            '<label class="cv2b-inline"><input type="checkbox" data-prop="thumbSpoiler"' +
            (node.spoiler ? " checked" : "") +
            "> Spoiler</label>"
          );
        case "section":
          return (
            '<div class="cv2b-insp-empty"><p>A section pairs <b>1–' +
            M.MAX_SECTION_TEXTS +
            " text blocks</b> with one accessory — a thumbnail or a link button.</p>" +
            "<p>Click the text on the left to edit it, or the accessory slot on the right to fill it.</p></div>"
          );
        default:
          return '<div class="cv2b-insp-empty">Nothing to edit here.</div>';
      }
    }

    function applyProp(prop, input) {
      const node = safeResolve(state.sel);
      if (!node) return;
      // One undo step per field-editing run, not per keystroke.
      const key = prop + pk(state.sel);
      if (liveKey !== key) {
        snapshot();
        liveKey = key;
      }
      const btnIndex = input.dataset && input.dataset.i ? Number(input.dataset.i) : 0;
      const b =
        M.kind(node) === "link_button" ? M.buttonsOf(node)[btnIndex] : null;
      switch (prop) {
        case "accent":
        case "accentHex": {
          const v = input.value.trim();
          const m = /^#?([0-9a-fA-F]{6})$/.exec(v);
          if (m) node.accent_color = parseInt(m[1], 16);
          else if (!v) delete node.accent_color;
          break;
        }
        case "accentOff":
          if (input.checked) delete node.accent_color;
          else node.accent_color = Number.isInteger(defaultAccent) ? defaultAccent : 0xec42a5;
          break;
        case "spoiler":
          node.spoiler = input.checked;
          break;
        case "content":
          node.content = input.value;
          break;
        case "divider":
          node.divider = input.checked;
          break;
        case "spacing":
          node.spacing = Number(input.value);
          break;
        case "mediaUrl": {
          const i = Number(input.dataset.i);
          if (!node.items[i]) node.items[i] = { media: { url: "" } };
          node.items[i].media = { url: input.value.trim() };
          break;
        }
        case "mediaAlt": {
          const i = Number(input.dataset.i);
          if (!node.items[i]) node.items[i] = { media: { url: "" } };
          if (input.value.trim()) node.items[i].description = input.value;
          else delete node.items[i].description;
          break;
        }
        case "mediaSpoiler": {
          const i = Number(input.dataset.i);
          if (!node.items[i]) node.items[i] = { media: { url: "" } };
          if (input.checked) node.items[i].spoiler = true;
          else delete node.items[i].spoiler;
          break;
        }
        case "btnLabel":
          b.label = input.value;
          break;
        case "btnUrl":
          b.url = input.value.trim();
          break;
        case "btnEmoji": {
          // A bare {name} only works for unicode; a custom guild emoji needs its id.
          const resolved = M.buttonEmojiFor(input.value, emoji);
          if (resolved) b.emoji = resolved;
          else delete b.emoji;
          break;
        }
        case "thumbUrl":
          node.media = { url: input.value.trim() };
          break;
        case "thumbDesc":
          if (input.value.trim()) node.description = input.value;
          else delete node.description;
          break;
        case "thumbSpoiler":
          node.spoiler = input.checked;
          break;
      }
      markDirty();
      // Repaint the canvas only — rebuilding the inspector would replace the input
      // being typed into and drop the caret.
      paintCanvas();
      el.undo.disabled = !state.undo.length;
    }

    el.inspector.addEventListener("input", (e) => {
      if (e.target.dataset.prop) applyProp(e.target.dataset.prop, e.target);
    });
    el.inspector.addEventListener("change", (e) => {
      if (e.target.dataset.prop) {
        applyProp(e.target.dataset.prop, e.target);
        liveKey = null;
        render();
      }
    });
    el.inspector.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      if (btn.dataset.a === "sheet-close") {
        sheetDismissed = true;
        syncSheet(false);
        return;
      }
      if (btn.dataset.problem !== undefined) {
        const p = state.problems[Number(btn.dataset.problem)];
        if (p && p.path) {
          state.sel = p.path;
          render();
          scrollToSel();
        }
        return;
      }
      const node = safeResolve(state.sel);
      if (!node) return;
      if (btn.dataset.act === "mediaAdd") {
        commit("Image added", () => {
          if (!node.items) node.items = [];
          node.items.push({ media: { url: "" } });
        });
      }
      if (btn.dataset.act === "mediaDel") {
        commit("Image removed", () => node.items.splice(Number(btn.dataset.i), 1));
      }
      if (btn.dataset.act === "btnAdd") {
        commit("Button added", () => {
          if (!node.components) node.components = [];
          node.components.push(M.makeButton());
        });
      }
      if (btn.dataset.act === "btnDel") {
        commit("Button removed", () =>
          node.components.splice(Number(btn.dataset.i), 1),
        );
      }
    });

    // ---- canvas interaction ----------------------------------------------------

    el.canvas.addEventListener("click", (e) => {
      if (shouldSwallow()) return; // the tail of a long-press, not a tap
      if (e.target.closest("[data-editing]")) return;

      const railEl = e.target.closest(".cv2b-rail");
      if (railEl) {
        openPalette(e, JSON.parse(railEl.dataset.scope), Number(railEl.dataset.index));
        return;
      }
      const slot = e.target.closest(".cv2b-acc-slot");
      if (slot) {
        openAccessoryMenu(e, JSON.parse(slot.dataset.accslot));
        return;
      }
      const blk = e.target.closest(".cv2b-blk");
      if (!blk) {
        state.sel = null;
        render();
        return;
      }
      const path = JSON.parse(blk.dataset.path);
      // Tapping a block is an explicit request to inspect it, so it re-opens a sheet
      // that was dismissed for this same block.
      sheetDismissed = false;
      // A click on words should put a caret there, not select a "block" and make you
      // click again.
      if (blk.dataset.kind === "text") {
        state.sel = path;
        startEdit(path);
        return;
      }
      state.sel = path;
      render();
    });

    el.canvas.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const blk = e.target.closest(".cv2b-blk");
      if (blk) {
        state.sel = JSON.parse(blk.dataset.path);
        render();
        openBlockMenu(e, state.sel);
      } else {
        openPalette(e, [], state.nodes.length);
      }
    });

    // ---- in-place text editing --------------------------------------------------
    // The editor is a contenteditable holding real <img> emoji, not a <textarea>. A
    // textarea can only hold characters, so the moment you clicked into a block every
    // emoji would revert to raw `<:name:123>` — the thing the builder exists to hide.
    //
    // Commit is SURGICAL: it swaps only the edited block's DOM. A full re-render here
    // would tear the DOM out from under an in-flight click, so clicking straight from one
    // text block into another would lose the second click.

    /** The editor's inner HTML for `content`: escaped text with emoji as images. */
    function editorHtml(content) {
      return M.emojiSegments(content, emoji)
        .map((seg) =>
          seg.type === "text"
            ? esc(seg.value)
            : '<img class="emoji cv2b-emoji" src="' +
              esc(seg.url) +
              '" alt="' +
              esc(seg.token) +
              '" data-token="' +
              esc(seg.token) +
              '" contenteditable="false">',
        )
        .join("");
    }

    const BLOCKISH = /^(DIV|P)$/;

    /**
     * Read the editor back to raw content.
     *
     * Emoji images carry the token they came from, so they round-trip to exactly the
     * text that produced them. Enter in a contenteditable produces <div>/<p> wrappers
     * (and `<div><br></div>` for a blank line) rather than "\n", so those are folded
     * back into newlines here.
     */
    function readEditor(root) {
      const parts = [];
      (function walk(node) {
        node.childNodes.forEach((child) => {
          if (child.nodeType === 3) {
            parts.push(child.nodeValue);
          } else if (child.nodeName === "IMG") {
            parts.push(child.getAttribute("data-token") || "");
          } else if (child.nodeName === "BR") {
            parts.push("\n");
          } else if (BLOCKISH.test(child.nodeName)) {
            if (parts.length) parts.push("\n");
            // `<div><br></div>` is one blank line, not two.
            const only = child.childNodes.length === 1 && child.firstChild;
            if (!(only && only.nodeName === "BR")) walk(child);
          } else {
            walk(child);
          }
        });
      })(root);
      return parts.join("");
    }

    function editorEl() {
      return el.canvas.querySelector("[data-editing]");
    }

    function startEdit(path) {
      state.editing = path;
      const node = safeResolve(path);
      if (!node) return;
      state.editOrigin = String(node.content || "");
      render();
      const box = editorEl();
      if (!box) return;
      box.focus();
      placeCaretAtEnd(box);

      box.addEventListener("input", () => updateEmojiPicker(box));
      box.addEventListener("keydown", (ev) => {
        if (emojiPickerKeydown(ev)) return; // the picker owns arrows/Enter/Esc while open
        if (
          (ev.key === "Backspace" || ev.key === "Delete") &&
          deleteEmojiAtom(box, ev.key === "Delete")
        ) {
          ev.preventDefault();
          return;
        }
        if (ev.key === "Escape") {
          ev.preventDefault();
          box.blur();
        }
        // Ctrl/Cmd+Enter finishes; plain Enter is a newline, as in a Discord message.
        if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
          ev.preventDefault();
          box.blur();
        }
      });
      // Paste as plain text: pasted markup would otherwise land as real HTML inside the
      // editor and readEditor would flatten it to something the author never typed.
      box.addEventListener("paste", (ev) => {
        ev.preventDefault();
        const text = (ev.clipboardData || window.clipboardData).getData("text");
        document.execCommand("insertText", false, text);
      });
      box.addEventListener("blur", () => {
        // A tap on the picker moves focus out of the editor; don't commit mid-pick.
        if (pickerEl && pickerEl.contains(document.activeElement)) return;
        closeEmojiPicker();
        commitEdit();
      });
    }

    /**
     * Delete the emoji atom on one side of a collapsed caret. Returns true when it did,
     * so the caller can suppress the browser's own delete.
     *
     * Chromium will not delete a contenteditable="false" <img> when the caret sits at an
     * ELEMENT-level offset beside it, and that is exactly where the caret lands after
     * accepting a suggestion at the end of a block. Backspace there is a silent no-op:
     * the emoji you just inserted cannot be removed at all, in either direction. Own the
     * atom rather than hoping the editing command grows the case.
     */
    function deleteEmojiAtom(box, forward) {
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return false;
      const at = sel.getRangeAt(0);
      if (!at.collapsed || !box.contains(at.startContainer)) return false;

      let node = at.startContainer;
      let offset = at.startOffset;
      // Normalise a caret resting against the edge of a text node up to its parent, so
      // "after the image" and "at offset 0 of the text after the image" are one case.
      if (node.nodeType === 3) {
        if (forward ? offset !== node.nodeValue.length : offset !== 0) return false;
        const i = Array.prototype.indexOf.call(node.parentNode.childNodes, node);
        offset = forward ? i + 1 : i;
        node = node.parentNode;
      }
      let index = forward ? offset : offset - 1;
      // A contenteditable strews empty text nodes about — accepting a suggestion at the
      // end of a block leaves one right after the image. They are not a character, so
      // step over them rather than spending a keypress on each.
      while (
        node.childNodes[index] &&
        node.childNodes[index].nodeType === 3 &&
        node.childNodes[index].nodeValue === ""
      ) {
        index += forward ? 1 : -1;
      }
      const atom = node.childNodes[index];
      if (!atom || atom.nodeName !== "IMG" || !atom.hasAttribute("data-token")) {
        return false;
      }
      // A live range placed where the atom is collapses onto the gap it leaves behind,
      // so the caret ends up exactly where the emoji was.
      const caret = document.createRange();
      caret.setStart(node, index);
      caret.collapse(true);
      atom.remove();
      sel.removeAllRanges();
      sel.addRange(caret);
      return true;
    }

    function placeCaretAtEnd(box) {
      const range = document.createRange();
      range.selectNodeContents(box);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }

    function commitEdit() {
      const box = editorEl();
      const path = state.editing;
      if (!box || !path) return;
      state.editing = null;
      const node = safeResolve(path);
      if (!node) {
        render();
        return;
      }
      const next = readEditor(box);
      if (next !== state.editOrigin) {
        // Snapshot the pre-edit tree so one editing session is one undo step.
        node.content = state.editOrigin;
        snapshot();
        node.content = next;
        markDirty();
        setStatus("Text updated");
      }
      const blk = box.closest(".cv2b-blk");
      if (blk) blk.outerHTML = renderBlock(path, node, problemPaths());
      refreshValidity();
    }

    // ---- emoji autocomplete ------------------------------------------------------
    // Typing ":" opens a picker over the guild emoji already loaded for the preview, so
    // an author never has to know a shortcode by heart or leave to look one up.

    let pickerEl = null;
    let pickerItems = [];
    let pickerIndex = 0;

    function closeEmojiPicker() {
      if (pickerEl) {
        pickerEl.remove();
        pickerEl = null;
      }
      pickerItems = [];
      pickerIndex = 0;
    }

    /** The text node + caret offset, if the caret is inside the editor's text. */
    function caretInText(box) {
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return null;
      const range = sel.getRangeAt(0);
      const node = range.startContainer;
      if (node.nodeType !== 3 || !box.contains(node)) return null;
      return { node, offset: range.startOffset };
    }

    function updateEmojiPicker(box) {
      const at = caretInText(box);
      if (!at) return closeEmojiPicker();
      const found = M.shortcodeBefore(at.node.nodeValue, at.offset);
      if (!found) return closeEmojiPicker();
      const items = M.emojiSuggestions(found.query, emoji, 8);
      if (!items.length) return closeEmojiPicker();
      renderEmojiPicker(items);
    }

    function renderEmojiPicker(items) {
      pickerItems = items;
      if (pickerIndex >= items.length) pickerIndex = 0;
      if (!pickerEl) {
        pickerEl = document.createElement("div");
        pickerEl.className = "cv2b-emoji-picker";
        document.body.appendChild(pickerEl);
        // pointerdown, not click: the editor's blur fires first on click and would
        // commit before the pick landed.
        pickerEl.addEventListener("pointerdown", (ev) => {
          const row = ev.target.closest("[data-i]");
          if (!row) return;
          ev.preventDefault();
          acceptSuggestion(pickerItems[Number(row.dataset.i)]);
        });
      }
      pickerEl.innerHTML = items
        .map(
          (s, i) =>
            '<button type="button" data-i="' +
            i +
            '" class="' +
            (i === pickerIndex ? "cv2b-pick-on" : "") +
            '"><img src="' +
            esc(s.url) +
            '" alt=""><span>' +
            esc(s.name) +
            "</span></button>",
        )
        .join("");
      positionEmojiPicker();
    }

    function positionEmojiPicker() {
      const sel = window.getSelection();
      const box = editorEl();
      if (!pickerEl || !box) return;
      let rect = null;
      if (sel && sel.rangeCount) {
        rect = sel.getRangeAt(0).getBoundingClientRect();
      }
      // A collapsed range can report a zero rect; fall back to the editor itself.
      if (!rect || (!rect.width && !rect.height)) rect = box.getBoundingClientRect();
      const pr = pickerEl.getBoundingClientRect();
      const left = Math.min(rect.left, window.innerWidth - pr.width - 8);
      // Prefer below the caret, flip above when there is no room (phone keyboards eat
      // the bottom half of the screen).
      const below = rect.bottom + 6;
      const top = below + pr.height > window.innerHeight - 8 ? rect.top - pr.height - 6 : below;
      pickerEl.style.left = Math.max(8, left) + "px";
      pickerEl.style.top = Math.max(8, top) + "px";
    }

    /** Returns true when the picker consumed the key. */
    function emojiPickerKeydown(ev) {
      if (!pickerEl || !pickerItems.length) return false;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        const delta = ev.key === "ArrowDown" ? 1 : -1;
        pickerIndex = (pickerIndex + delta + pickerItems.length) % pickerItems.length;
        renderEmojiPicker(pickerItems);
        return true;
      }
      if (ev.key === "Enter" || ev.key === "Tab") {
        ev.preventDefault();
        acceptSuggestion(pickerItems[pickerIndex]);
        return true;
      }
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeEmojiPicker();
        return true;
      }
      return false;
    }

    /** Replace the `:partial` before the caret with the chosen emoji image. */
    function acceptSuggestion(suggestion) {
      const box = editorEl();
      if (!box || !suggestion) return closeEmojiPicker();
      const at = caretInText(box);
      if (!at) return closeEmojiPicker();
      const found = M.shortcodeBefore(at.node.nodeValue, at.offset);
      if (!found) return closeEmojiPicker();

      const text = at.node.nodeValue;
      const tail = document.createTextNode(text.slice(at.offset));
      const img = document.createElement("img");
      img.className = "emoji cv2b-emoji";
      img.src = suggestion.url;
      img.alt = suggestion.token;
      img.setAttribute("data-token", suggestion.token);
      img.contentEditable = "false";

      const parent = at.node.parentNode;
      at.node.nodeValue = text.slice(0, found.start);
      parent.insertBefore(tail, at.node.nextSibling);
      parent.insertBefore(img, tail);

      const range = document.createRange();
      range.setStart(tail, 0);
      range.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);

      closeEmojiPicker();
      box.focus();
      markDirty();
    }

    function scrollToSel() {
      if (!state.sel) return;
      const want = pk(state.sel);
      const found = Array.prototype.find.call(
        el.canvas.querySelectorAll(".cv2b-blk"),
        (n) => n.dataset.path === want,
      );
      if (found) found.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    // ---- drag & drop (pointer events) -------------------------------------------
    // Deliberately NOT HTML5 drag-and-drop: dragstart/dragover/drop never fire on
    // touch, so the whole rearranging model was dead on mobile. Pointer events cover
    // mouse, touch and pen in one code path, so there is no desktop/mobile fork here.
    //
    // Drop targets stay the insertion rails and accessory slots, hit-tested with
    // elementFromPoint, so the drop point is always exact rather than "nearest guess".
    // An illegal rail turns red and says why.

    let drag = null; // {kind, from, ghost, moved}
    let longPress = null; // pending touch long-press -> context menu
    // A touch that opens the long-press menu is still followed by synthesized
    // mouse/click events on release. Without swallowing those, the menu is dismissed by
    // the very gesture that opened it, and the block underneath gets tapped as well.
    let swallowUntil = 0;
    const swallowSyntheticClick = () => {
      swallowUntil = Date.now() + 700;
    };
    const shouldSwallow = () => Date.now() < swallowUntil;
    const DRAG_THRESHOLD = 6; // px before a press becomes a drag (vs. a tap)
    const LONG_PRESS_MS = 450;
    const EDGE = 60; // autoscroll band at the top/bottom of the canvas
    const AUTOSCROLL_PX = 10; // per frame, while the pointer rests in that band
    const lastPoint = { x: 0, y: 0 };
    let scrollDir = 0;
    let scrollRaf = 0;

    function makeGhost(label, x, y) {
      const ghost = document.createElement("div");
      ghost.className = "cv2b-ghost";
      ghost.textContent = label;
      document.body.appendChild(ghost);
      moveGhost(ghost, x, y);
      return ghost;
    }
    function moveGhost(ghost, x, y) {
      ghost.style.left = x + "px";
      ghost.style.top = y + "px";
    }

    function beginDrag(kind, from, x, y) {
      cancelLongPress();
      const label = from
        ? M.KIND_LABEL[kind] || kind
        : "New " + (M.KIND_LABEL[kind] || kind).toLowerCase();
      // `target` is the armed drop site, the single answer both the highlight and the
      // release read (see armTarget). `rails`/`railsAt` memoize validRailRects.
      drag = { kind, from, ghost: makeGhost(label, x, y), target: null };
      document.body.classList.add("cv2b-dragging-now");
      if (from) {
        const el0 = blockEl(from);
        if (el0) el0.classList.add("cv2b-dragging");
      }
      markValidTargets();
    }

    function blockEl(path) {
      const want = pk(path);
      return Array.prototype.find.call(
        el.canvas.querySelectorAll(".cv2b-blk"),
        (n) => n.dataset.path === want,
      );
    }

    /** Mark every rail as soon as something is picked up, so the legal drops are
     *  visible before you go hunting — you learn the rules by looking, not by failing. */
    function markValidTargets() {
      if (!drag) return;
      el.canvas.querySelectorAll(".cv2b-rail").forEach((r) => {
        const scope = JSON.parse(r.dataset.scope);
        r.classList.toggle(
          "cv2b-blocked",
          !M.canDrop(state.nodes, scope, drag.kind, drag.from),
        );
      });
      const accOk = M.isAccessoryKind(drag.kind);
      el.canvas
        .querySelectorAll(".cv2b-acc-slot")
        .forEach((s) => s.classList.toggle("cv2b-blocked", !accOk));
    }

    function clearDragMarks() {
      el.canvas
        .querySelectorAll(".cv2b-dragging, .cv2b-armed, .cv2b-blocked")
        .forEach((n) =>
          n.classList.remove("cv2b-dragging", "cv2b-armed", "cv2b-blocked"),
        );
      document.body.classList.remove("cv2b-dragging-now");
    }

    /** Rails that are LEGAL for this drag, measured.
     *
     *  Blocked-ness is fixed for the life of a drag — markValidTargets computes it at
     *  pickup from nodes/kind/from, none of which change while a block is in the air — so
     *  only the geometry needs refreshing, and only when the canvas scrolls under the
     *  pointer. NOTE for the gap-opening step: an animating rail height moves these rects
     *  WITHOUT changing scrollTop, so that change has to invalidate this cache too. */
    function validRailRects() {
      const scrollTop = el.canvas.parentElement.scrollTop;
      if (drag.rails && drag.railsAt === scrollTop) return drag.rails;
      const rects = [];
      el.canvas.querySelectorAll(".cv2b-rail:not(.cv2b-blocked)").forEach((r) => {
        const box = r.getBoundingClientRect();
        rects.push({
          el: r,
          top: box.top,
          bottom: box.bottom,
          left: box.left,
          right: box.right,
        });
      });
      drag.rails = rects;
      drag.railsAt = scrollTop;
      return rects;
    }

    /** Where an otherwise-missed release should land, or null when it is far enough away
     *  to read as a deliberate cancel. */
    function nearestRailTarget(x, y) {
      const rects = validRailRects();
      // The previously armed rail gets the hysteresis margin, so the target does not
      // flicker between neighbours while the pointer sits still.
      const armed =
        drag.target && drag.target.kind === "rail"
          ? rects.findIndex((r) => r.el === drag.target.el)
          : -1;
      const i = M.nearestRail(rects, x, y, armed);
      if (i === -1) return null;
      const rail = rects[i].el;
      return {
        el: rail,
        kind: "rail",
        scope: JSON.parse(rail.dataset.scope),
        index: Number(rail.dataset.index),
      };
    }

    /** The drop target for a point, or null. The ghost is pointer-events:none so it never
     *  hit-tests as itself.
     *
     *  An exact hit always wins — including on a BLOCKED rail, because hovering an illegal
     *  target has to keep explaining itself through the refusal toast; that is how the
     *  nesting rules are taught. Only when the pointer is over nothing at all does the
     *  nearest legal rail take over. A rail is 0.62rem tall, so on touch "over nothing"
     *  is the common case, and it used to mean the block sprang back unexplained. */
    function targetAt(x, y) {
      const under = document.elementFromPoint(x, y);
      if (under) {
        const rail = under.closest(".cv2b-rail");
        if (rail) {
          const scope = JSON.parse(rail.dataset.scope);
          return M.canDrop(state.nodes, scope, drag.kind, drag.from)
            ? { el: rail, kind: "rail", scope, index: Number(rail.dataset.index) }
            : { el: rail, kind: "blocked", scope };
        }
        const slot = under.closest(".cv2b-acc-slot");
        if (slot && M.isAccessoryKind(drag.kind)) {
          return {
            el: slot,
            kind: "acc",
            sectionPath: JSON.parse(slot.dataset.accslot),
          };
        }
      }
      return nearestRailTarget(x, y);
    }

    /** Highlight (or refuse) the drop target for a point, and record it as the one the
     *  release will act on.
     *
     *  Storing it is what keeps the drop honest: endDrag used to hit-test again on
     *  pointerup, so the highlight and the landing site were two independent answers to
     *  the same question. They agreed only by coincidence — autoscroll re-arms against
     *  freshly scrolled content, and the nearest search deliberately holds a target
     *  through small movements. Whenever they disagree, the block lands somewhere other
     *  than the rail the author is looking at, which is the worst way for a drag tool to
     *  be wrong. One answer, computed here, consumed there. */
    function armTarget(x, y) {
      el.canvas
        .querySelectorAll(".cv2b-armed")
        .forEach((n) => n.classList.remove("cv2b-armed"));
      const target = targetAt(x, y);
      drag.target = target;
      if (!target) return hideToast();
      if (target.kind === "blocked") {
        toast(M.refusalReason(state.nodes, target.scope, drag.kind), true);
        return;
      }
      target.el.classList.add("cv2b-armed");
      hideToast();
    }

    // Autoscroll runs on its own frame loop rather than off pointermove. A finger (or a
    // mouse) held still at the edge of the canvas emits no further events, so a
    // move-driven scroll stopped dead exactly when the author was asking it to keep
    // going — on a phone, where the message is several viewports tall, that meant
    // jiggling at the edge dozens of times to reach the top.
    function setAutoScroll(dir) {
      if (dir === scrollDir) return;
      scrollDir = dir;
      if (dir && !scrollRaf) scrollRaf = requestAnimationFrame(autoScrollStep);
      if (!dir && scrollRaf) {
        cancelAnimationFrame(scrollRaf);
        scrollRaf = 0;
      }
    }

    function autoScrollStep() {
      scrollRaf = 0;
      if (!drag || !scrollDir) return;
      const wrap = el.canvas.parentElement;
      const before = wrap.scrollTop;
      wrap.scrollTop = before + scrollDir * AUTOSCROLL_PX;
      // The content moved under a stationary pointer, so the armed rail has to be
      // re-hit-tested or the drop lands where the target used to be.
      if (wrap.scrollTop !== before) armTarget(lastPoint.x, lastPoint.y);
      scrollRaf = requestAnimationFrame(autoScrollStep);
    }

    function updateDrag(x, y) {
      lastPoint.x = x;
      lastPoint.y = y;
      moveGhost(drag.ghost, x, y);
      const box = el.canvas.parentElement.getBoundingClientRect();
      setAutoScroll(y < box.top + EDGE ? -1 : y > box.bottom - EDGE ? 1 : 0);
      armTarget(x, y);
    }

    function endDrag(x, y) {
      const d = drag;
      setAutoScroll(0);
      // Arm once more at the release point, then act on what is ARMED — never on an
      // independent hit test (see armTarget). Re-arming means a release that drifted a
      // few pixels off the last move is still accounted for, while hysteresis stops a
      // small roll of the finger on lift-off from changing where the block lands.
      armTarget(x, y);
      const target = d.target;
      const ghost = d.ghost;
      // Measure the landing site BEFORE clearing the drag marks — an armed rail may be
      // holding a gap open, and clearing it closes that gap.
      const landing =
        target && target.el && target.kind !== "blocked"
          ? target.el.getBoundingClientRect()
          : null;
      drag = null;
      clearDragMarks();
      hideToast();
      if (!target || target.kind === "blocked") {
        if (ghost) ghost.remove();
        render();
        return;
      }
      if (target.kind === "rail") {
        return flyGhostTo(ghost, landing, () =>
          commit(d.from ? "Moved" : M.KIND_LABEL[d.kind] + " added", () => {
            state.sel = d.from
              ? M.moveNode(state.nodes, d.from, target.scope, target.index)
              : M.insertAt(
                  state.nodes,
                  target.scope,
                  target.index,
                  M.makeNode(d.kind, defaultAccent),
                );
          }),
        );
      }
      const node = d.from
        ? clone(M.resolve(state.nodes, d.from))
        : M.makeNode(d.kind, defaultAccent);
      // An accessory holds exactly one button, so dragging a row of several in keeps
      // the first and drops the rest. Say so — otherwise the buttons vanish between two
      // frames and the author is left wondering whether they were ever there.
      const dropped = M.buttonsOf(node).length - 1;
      flyGhostTo(ghost, landing, () =>
        commit("Accessory set", () => {
          // Hold the section by reference: removing the source can rebase its path, but
          // the object itself never moves.
          const section = M.resolve(state.nodes, target.sectionPath);
          let at = target.sectionPath;
          if (d.from) {
            M.removeAt(state.nodes, d.from);
            at = M.adjustAfterRemoval(target.sectionPath, d.from);
          }
          section.accessory = node.type === M.ACTION_ROW ? node.components[0] : node;
          state.sel = at.concat(["acc"]);
        }),
      );
      if (dropped > 0) {
        toast(
          "An accessory holds one button — the other " +
            (dropped > 1 ? dropped + " were" : "one was") +
            " dropped.",
          true,
        );
      }
    }

    /** Fly the ghost to where the block will land, then apply the mutation.
     *
     *  Without it the ghost vanishes at the pointer while the block reappears somewhere
     *  else in the same frame, so the one thing the author most needs to read — where did
     *  it go? — is the one thing never shown. Step 3 is what makes this safe to draw: the
     *  landing site is the ARMED target, so the ghost cannot promise a destination the
     *  block will not actually take.
     *
     *  `settle` is deferred by the flight's duration. Nothing else mutates state.nodes in
     *  that window (~140ms, and the author has just lifted their finger), and the paths it
     *  closes over stay valid against an unmodified tree.
     *
     *  Reduced motion, no WAAPI, or no landing site: commit immediately. */
    function flyGhostTo(ghost, landing, settle) {
      if (!ghost || !landing || !ghost.animate || reduceMotion()) {
        if (ghost) ghost.remove();
        return settle();
      }
      const flight = ghost.animate(
        [
          {
            left: ghost.style.left,
            top: ghost.style.top,
            // The ghost normally rides above the pointer (see .cv2b-ghost); dropping that
            // offset as it arrives is what makes it settle ONTO the rail rather than hover
            // over it.
            transform: "translate(-50%, -160%)",
          },
          {
            left: landing.left + landing.width / 2 + "px",
            top: landing.top + landing.height / 2 + "px",
            transform: "translate(-50%, -50%)",
          },
        ],
        { duration: motionMs("--dur"), easing: EASE_OUT, fill: "forwards" },
      );
      const land = () => {
        settle();
        // Cross-dissolve into the landing flash rather than cutting: the ghost is
        // position:fixed, so it fades out on top of the real block fading in. Cutting
        // leaves a frame where neither is drawn.
        const fade = ghost.animate([{ opacity: 1 }, { opacity: 0 }], {
          duration: motionMs("--dur"),
          easing: EASE_OUT,
        });
        const drop = () => ghost.remove();
        fade.finished.then(drop, drop);
      };
      // Commit on cancel too, so an interrupted flight can never swallow the drop.
      flight.finished.then(land, land);
    }

    function cancelDrag() {
      if (!drag) return;
      setAutoScroll(0);
      if (drag.ghost) drag.ghost.remove();
      drag = null;
      clearDragMarks();
      hideToast();
      render();
    }

    function cancelLongPress() {
      if (longPress) {
        clearTimeout(longPress.timer);
        longPress = null;
      }
    }

    // A press starts a *pending* gesture; what it becomes depends on what happens next.
    // Moving past the threshold makes it a drag; holding still makes it a long-press
    // context menu (the touch equivalent of right-click); releasing makes it a tap,
    // which the existing click handler deals with.
    function onPointerDown(e, kind, from, gripOnly) {
      if (e.button !== undefined && e.button > 0) return; // right-click stays right-click
      const startX = e.clientX;
      const startY = e.clientY;
      let started = false;

      const move = (ev) => {
        const dx = Math.abs(ev.clientX - startX);
        const dy = Math.abs(ev.clientY - startY);
        if (!started && (dx > DRAG_THRESHOLD || dy > DRAG_THRESHOLD)) {
          // A press on the block body (not the grip) that turns into a move is a scroll
          // on touch, not a drag — only the grip and the palette initiate dragging.
          if (!gripOnly) {
            cleanup();
            return;
          }
          started = true;
          beginDrag(kind, from, ev.clientX, ev.clientY);
        }
        if (started) {
          ev.preventDefault();
          updateDrag(ev.clientX, ev.clientY);
        }
      };
      const up = (ev) => {
        cleanup();
        if (started) endDrag(ev.clientX, ev.clientY);
      };
      const cancel = () => {
        cleanup();
        cancelDrag();
      };
      function cleanup() {
        cancelLongPress();
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", cancel);
      }
      window.addEventListener("pointermove", move, { passive: false });
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", cancel);

      // Touch has no right-click, so a hold opens the same context menu.
      if (e.pointerType === "touch" && from) {
        cancelLongPress();
        longPress = {
          timer: setTimeout(() => {
            longPress = null;
            if (started) return;
            cleanup();
            swallowSyntheticClick();
            state.sel = from;
            render();
            openBlockMenu({ clientX: startX, clientY: startY }, from);
          }, LONG_PRESS_MS),
        };
      }
    }

    el.palette.addEventListener("pointerdown", (e) => {
      const item = e.target.closest(".cv2b-pal");
      if (!item) return;
      onPointerDown(e, item.dataset.kind, null, true);
    });

    el.canvas.addEventListener("pointerdown", (e) => {
      if (e.target.closest("[data-editing]")) return;
      const blk = e.target.closest(".cv2b-blk");
      if (!blk) return;
      const path = JSON.parse(blk.dataset.path);
      const onGrip = !!e.target.closest(".cv2b-grip");
      onPointerDown(e, blk.dataset.kind, path, onGrip);
    });
    // ---- context menus ----------------------------------------------------------

    let menuEl = null;
    function closeMenu() {
      if (menuEl) {
        menuEl.remove();
        menuEl = null;
      }
    }
    document.addEventListener("mousedown", (e) => {
      if (shouldSwallow()) return;
      if (menuEl && !e.target.closest(".cv2b-menu")) closeMenu();
    });

    function showMenu(x, y, html) {
      closeMenu();
      menuEl = document.createElement("div");
      menuEl.className = "cv2b-menu";
      menuEl.innerHTML = html;
      document.body.appendChild(menuEl);
      const r = menuEl.getBoundingClientRect();
      menuEl.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
      menuEl.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
      return menuEl;
    }
    const mi = (act, glyph, label, kbd, cls) =>
      '<button type="button" data-act="' +
      act +
      '" class="' +
      (cls || "") +
      '"><span class="cv2b-glyph">' +
      glyph +
      "</span>" +
      esc(label) +
      '<span class="cv2b-kbd">' +
      (kbd || "") +
      "</span></button>";

    function openPalette(e, scope, index, label) {
      // Filter by canDrop, not allowedIn: inside a section that already holds its three
      // text blocks, "text" is an allowed *kind* but not an allowed *insert* — offering
      // it would let the menu build a tree the validator immediately rejects.
      const items = PALETTE.filter((p) =>
        M.canDrop(state.nodes, scope, p.kind, null),
      )
        .map((p) => mi("add:" + p.kind, p.glyph, p.label))
        .join("");
      if (!items) {
        toast(M.refusalReason(state.nodes, scope, "text"), true);
        return;
      }
      const menu = showMenu(
        e.clientX,
        e.clientY,
        '<div class="cv2b-menu-label">' +
          esc(label || "Add here") +
          "</div>" +
          items,
      );
      menu.addEventListener("click", (ev) => {
        const b = ev.target.closest("button");
        if (!b) return;
        const k = b.dataset.act.slice(4);
        closeMenu();
        commit(M.KIND_LABEL[k] + " added", () => {
          state.sel = M.insertAt(
            state.nodes,
            scope,
            index,
            M.makeNode(k, defaultAccent),
          );
        });
        if (k === "text") startEdit(state.sel);
      });
    }

    function openAccessoryMenu(e, sectionPath) {
      const menu = showMenu(
        e.clientX,
        e.clientY,
        '<div class="cv2b-menu-label">Section accessory</div>' +
          mi("acc:thumbnail", "▣", "Thumbnail image") +
          mi("acc:link_button", "⬒", "Link button"),
      );
      menu.addEventListener("click", (ev) => {
        const b = ev.target.closest("button");
        if (!b) return;
        const k = b.dataset.act.slice(4);
        closeMenu();
        commit("Accessory added", () => {
          state.sel = M.setAccessory(
            state.nodes,
            sectionPath,
            M.makeNode(k, defaultAccent),
          );
        });
      });
    }

    function openBlockMenu(e, path) {
      const node = M.resolve(state.nodes, path);
      const k = M.kind(node);
      const scope = path.slice(0, -1);
      const idx = path[path.length - 1];
      const isAcc = idx === "acc";
      const list = isAcc ? [] : M.childList(state.nodes, scope);
      // Wrapping only makes sense at the top level: containers cannot nest.
      const canWrap = !isAcc && !scope.length && k !== "container";

      const menu = showMenu(
        e.clientX,
        e.clientY,
        [
          k === "text" ? mi("edit", "✎", "Edit text", "Click") : "",
          isAcc ? "" : mi("dup", "⧉", "Duplicate", "Ctrl+D"),
          canWrap ? mi("wrap", "▤", "Wrap in a container") : "",
          "<hr>",
          isAcc ? "" : mi("above", "↑", "Add block above"),
          isAcc ? "" : mi("below", "↓", "Add block below"),
          isAcc || list.length < 2 ? "" : mi("top", "⤒", "Move to top"),
          isAcc || list.length < 2 ? "" : mi("bottom", "⤓", "Move to bottom"),
          "<hr>",
          mi("del", "✕", isAcc ? "Remove accessory" : "Delete", "Del", "cv2b-danger"),
        ].join(""),
      );

      menu.addEventListener("click", (ev) => {
        const b = ev.target.closest("button");
        if (!b) return;
        const act = b.dataset.act;
        closeMenu();
        if (act === "edit") return startEdit(path);
        if (act === "above") return openPalette(e, scope, idx);
        if (act === "below") return openPalette(e, scope, idx + 1);
        if (act === "dup") {
          return commit("Duplicated", () => {
            state.sel = M.insertAt(state.nodes, scope, idx + 1, clone(node));
          });
        }
        if (act === "wrap") {
          return commit("Wrapped in a container", () => {
            const wrapper = M.makeContainer(defaultAccent);
            wrapper.components = [clone(node)];
            M.childList(state.nodes, scope).splice(idx, 1, wrapper);
            state.sel = scope.concat([idx]);
          });
        }
        if (act === "top") {
          return commit("Moved to top", () => {
            state.sel = M.moveNode(state.nodes, path, scope, 0);
          });
        }
        if (act === "bottom") {
          return commit("Moved to bottom", () => {
            state.sel = M.moveNode(state.nodes, path, scope, list.length);
          });
        }
        if (act === "del") {
          return commitAfterCollapse(path, "Deleted", () => {
            state.sel = M.removeAt(state.nodes, path);
          });
        }
      });
    }

    // ---- keyboard ---------------------------------------------------------------

    root.addEventListener("keydown", onKey);
    document.addEventListener("keydown", onKey);

    function onKey(e) {
      if (!root.isConnected) return;
      // isContentEditable is load-bearing since the inline editor became a <div>:
      // without it Backspace here deletes the whole BLOCK instead of a character,
      // and Enter inserts a new block instead of a newline.
      const typing =
        /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) ||
        !!(e.target && e.target.isContentEditable);
      const mod = e.metaKey || e.ctrlKey;

      if (e.key === "Escape" && menuEl) {
        closeMenu();
        return;
      }
      if (mod && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (mod && e.key.toLowerCase() === "y" && !typing) {
        e.preventDefault();
        redo();
        return;
      }
      if (typing) return;

      if (mod && e.key.toLowerCase() === "d" && state.sel) {
        const idx = state.sel[state.sel.length - 1];
        if (idx === "acc") return;
        e.preventDefault();
        const scope = state.sel.slice(0, -1);
        const node = clone(M.resolve(state.nodes, state.sel));
        commit("Duplicated", () => {
          state.sel = M.insertAt(state.nodes, scope, idx + 1, node);
        });
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && state.sel) {
        e.preventDefault();
        commitAfterCollapse(state.sel, "Deleted", () => {
          state.sel = M.removeAt(state.nodes, state.sel);
        });
        return;
      }
      if (e.key === "Enter" && state.sel) {
        const idx = state.sel[state.sel.length - 1];
        if (idx === "acc") return;
        const scope = state.sel.slice(0, -1);
        if (!M.canDrop(state.nodes, scope, "text", null)) return;
        e.preventDefault();
        commit("Text added", () => {
          state.sel = M.insertAt(state.nodes, scope, idx + 1, M.makeText(""));
        });
        startEdit(state.sel);
        return;
      }
      if (e.key === "Escape") {
        state.sel = null;
        render();
      }
    }

    // ---- toolbar / toast / publish ----------------------------------------------

    let toastTimer = null;
    function toast(msg, warn) {
      el.toast.textContent = msg;
      el.toast.classList.toggle("cv2b-warn", !!warn);
      el.toast.classList.add("cv2b-show");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(hideToast, 2400);
    }
    function hideToast() {
      el.toast.classList.remove("cv2b-show");
    }
    function setStatus(msg, isError) {
      el.status.textContent = msg || "";
      el.status.classList.toggle("cv2b-err", !!isError);
      syncStatusLink();
    }
    /** Status text carrying a link, e.g. the posted message. */
    function setStatusLink(html) {
      el.status.innerHTML = html;
      el.status.classList.remove("cv2b-err");
      syncStatusLink();
    }
    // The status line is hidden on mobile because it carries desktop-only advice —
    // except once it holds the posted-message link, which is the one thing you do want
    // to tap. Every writer of el.status goes through setStatus/setStatusLink so that
    // exception cannot be forgotten by one of them.
    function syncStatusLink() {
      el.status.classList.toggle(
        "cv2b-has-link",
        el.status.querySelector("a") !== null,
      );
    }

    el.undo.addEventListener("click", undo);
    el.redo.addEventListener("click", redo);

    // The collapsed "Add" control (small screens only — the palette rail is hidden
    // there). A permanent list of six block types costs a band of vertical space on
    // every screen for something used a handful of times per message, and vertical space
    // is the scarce resource on a phone. Behind one button it costs nothing until asked
    // for, and it reuses the exact menu the "+" rails and right-click already open, so
    // there is one code path for "choose a block type" rather than two.
    //
    // Where it lands is the same rule as clicking a palette item on desktop — after the
    // selection, or at the end — but stated in the menu header, because a button far
    // from the insertion point has to say where it will drop things.
    function addTarget() {
      const sel = state.sel;
      if (sel && sel[sel.length - 1] !== "acc") {
        const scope = sel.slice(0, -1);
        const index = sel[sel.length - 1] + 1;
        // Only honour the selection if something can actually go beside it (a full
        // section can't take a fourth text block); otherwise fall back to the end.
        const anyLegal = PALETTE.some((p) =>
          M.canDrop(state.nodes, scope, p.kind, null),
        );
        if (anyLegal) {
          const kindLabel = M.KIND_LABEL[M.kind(M.resolve(state.nodes, sel))] || "block";
          return { scope, index, label: "Add after this " + kindLabel.toLowerCase() };
        }
      }
      return {
        scope: [],
        index: state.nodes.length,
        label: state.nodes.length ? "Add at the end" : "Add the first block",
      };
    }

    el.add.addEventListener("click", () => {
      const box = el.add.getBoundingClientRect();
      const target = addTarget();
      // Anchor under the button rather than at the pointer, so it reads as that
      // button's menu.
      openPalette(
        { clientX: box.left, clientY: box.bottom + 6 },
        target.scope,
        target.index,
        target.label,
      );
    });

    el.publish.addEventListener("click", async () => {
      if (state.problems.length) {
        const first = state.problems.find((p) => p.path);
        if (first) {
          state.sel = first.path;
          render();
          scrollToSel();
        }
        toast(
          state.problems.length +
            " problem" +
            (state.problems.length > 1 ? "s" : "") +
            " to fix first",
          true,
        );
        return;
      }
      await flush();
      // The server render is the authoritative one — confirm against it, not against
      // the client's approximation of the markdown.
      if (options.onPreview) {
        el.confirmBody.innerHTML = '<p class="cv2b-help">Rendering…</p>';
        el.dialog.showModal();
        try {
          el.confirmBody.innerHTML = await options.onPreview(clone(state.nodes));
        } catch (err) {
          el.confirmBody.innerHTML =
            '<p class="cv2b-err">Could not render a preview. You can still post.</p>';
        }
      } else {
        el.dialog.showModal();
      }
    });

    root.querySelector('[data-a="cancel-confirm"]').addEventListener("click", () => {
      el.dialog.close();
    });

    root.querySelector('[data-a="confirm"]').addEventListener("click", async () => {
      const btn = root.querySelector('[data-a="confirm"]');
      btn.disabled = true;
      try {
        const result = await options.onPublish(clone(state.nodes));
        el.dialog.close();
        state.dirty = false;
        if (result && result.link) {
          setStatusLink(
            'Posted — <a href="' +
              esc(result.link) +
              '" target="_blank" rel="noopener noreferrer">open in Discord</a>',
          );
        } else {
          setStatus("Posted");
        }
        el.publish.disabled = true;
      } catch (err) {
        el.confirmBody.innerHTML =
          '<p class="cv2b-err">' +
          esc((err && err.message) || "Discord rejected the message.") +
          "</p>";
      } finally {
        btn.disabled = false;
      }
    });

    // Palette rail
    PALETTE.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "cv2b-pal";
      b.dataset.kind = p.kind;
      b.title = p.hint;
      b.innerHTML = '<span class="cv2b-glyph">' + p.glyph + "</span>" + esc(p.label);
      b.addEventListener("click", () => {
        // Click = "put it after what I have selected" — the one behaviour worth keeping
        // from the in-Discord builder (_insert_at_selection).
        let scope = [];
        let index = state.nodes.length;
        if (state.sel && state.sel[state.sel.length - 1] !== "acc") {
          const s = state.sel.slice(0, -1);
          if (M.canDrop(state.nodes, s, p.kind, null)) {
            scope = s;
            index = state.sel[state.sel.length - 1] + 1;
          }
        }
        if (!M.canDrop(state.nodes, scope, p.kind, null)) {
          toast(M.refusalReason(state.nodes, scope, p.kind), true);
          return;
        }
        commit(p.label + " added", () => {
          state.sel = M.insertAt(state.nodes, scope, index, M.makeNode(p.kind, defaultAccent));
        });
        if (p.kind === "text") startEdit(state.sel);
      });
      el.palette.insertBefore(b, el.palette.querySelector(".cv2b-pal-hint"));
    });

    render();
    setStatus("Right-click anything · Ctrl/Cmd+Z undoes");

    return {
      getNodes: () => clone(state.nodes),
      setNodes: (nodes) => {
        state.nodes = clone(nodes);
        state.sel = null;
        state.undo.length = 0;
        state.redo.length = 0;
        render();
      },
    };
  }

  function SHELL_HTML(actionLabel) {
    return (
      '<div class="cv2b">' +
      '<header class="cv2b-bar">' +
      '<button type="button" class="cv2b-icon" data-a="undo" title="Undo (Ctrl/Cmd+Z)">↶</button>' +
      '<button type="button" class="cv2b-icon" data-a="redo" title="Redo (Ctrl/Cmd+Shift+Z)">↷</button>' +
      '<button type="button" class="cv2b-add" data-a="add">+ Add</button>' +
      '<span class="cv2b-status"></span>' +
      '<span class="cv2b-grow"></span>' +
      '<span class="cv2b-count"></span>' +
      '<button type="button" class="cv2b-primary" data-a="publish">' +
      M.esc(actionLabel) +
      "</button>" +
      "</header>" +
      '<div class="cv2b-main">' +
      '<nav class="cv2b-palette"><div class="cv2b-rail-title">Blocks</div>' +
      '<div class="cv2b-pal-hint">Drag onto the message, or click to drop it after the selection.' +
      "<br><br><kbd>right-click</kbd> anything for its actions.</div></nav>" +
      '<div class="cv2b-canvas-wrap"><div class="cv2b-canvas cv2-preview"></div></div>' +
      '<aside class="cv2b-inspector"></aside>' +
      "</div>" +
      '<div class="cv2b-toast"></div>' +
      '<dialog class="cv2b-confirm">' +
      '<div class="cv2b-dlg-head"><h3>Ready to send?</h3>' +
      '<span class="cv2b-help">This is exactly how Discord will render it.</span>' +
      '<span class="cv2b-grow"></span>' +
      '<button type="button" data-a="cancel-confirm">Back</button>' +
      '<button type="button" class="cv2b-primary" data-a="confirm">' +
      M.esc(actionLabel) +
      "</button></div>" +
      '<div class="cv2b-confirm-body cv2-preview"></div>' +
      "</dialog>" +
      "</div>"
    );
  }

  window.initCv2Builder = initCv2Builder;
})();
