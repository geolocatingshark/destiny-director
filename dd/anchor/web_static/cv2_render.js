// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// The shared Discord-message renderer — a CV2 component tree (or a classic
// content+embeds payload) drawn as the post it will be.
//
// WHY THIS FILE EXISTS
// --------------------
// The same message used to be rendered twice, in two languages: Python walked the tree
// into an HTML string for the mirror log and the publish confirmation, while
// cv2_builder.js built its own markup for the builder canvas. Two implementations of one
// picture drift, and they had — a backtick code span rendered one way on the canvas and
// another in the dialog that claims to show exactly what Discord will render. The canvas
// repaints per keystroke and cannot round-trip to a server, so the JavaScript side is
// the one that survives. See docs/architecture.md, "Rendering a message on the web".
//
// IT EMITS A SPEC, NOT MARKUP
// ---------------------------
// The walker is pure: node tree in, plain-data spec tree out. Two thin back ends turn a
// spec into something:
//
//   serialize(spec)   -> an HTML string.  Pure, so `node --test` can assert it with no
//                        DOM, and byte-compatible with the Python renderer so the shared
//                        golden corpus (dd/anchor/preview_fixtures) can hold both to one
//                        output while the port is in flight.
//   materialize(spec) -> real DOM.  Browser only. This is the one pages use.
//
// That split is what makes the safety structural rather than remembered. The Python it
// replaces re-states the http(s) check at six separate emit sites; here a URL is checked
// once, in one place, and a text leaf goes to `textContent` where an injection is not
// expressible. Exactly one field, `md`, is allowed to reach innerHTML, and only ever
// with cv2_model.renderMd output — the escape-by-construction markdown island.
//
// It matters more than it used to: the mirror log renders OTHER PEOPLE'S captured posts,
// so this walker's input is attacker-controlled in production.

(function () {
  "use strict";

  const M =
    typeof require !== "undefined"
      ? require("./cv2_model.js")
      : window.CV2Model;

  // Discord component type ids. Duplicated from cv2_model rather than imported through
  // it so the two files stay independently readable; cv2_nodes.py duplicates them too,
  // for the same reason.
  const ACTION_ROW = 1;
  const BUTTON = 2;
  const SECTION = 9;
  const TEXT_DISPLAY = 10;
  const THUMBNAIL = 11;
  const MEDIA_GALLERY = 12;
  const FILE = 13;
  const SEPARATOR = 14;
  const CONTAINER = 17;

  const EMOJI_CDN = "https://cdn.discordapp.com/emojis/";

  // The item count drives the grid layout, so galleries split the way Discord's do
  // rather than stacking full-width.
  const MEDIA_LAYOUT = { 1: "n1", 2: "n2", 3: "n3", 4: "n4" };

  // --- url + colour validation ---------------------------------------------------------

  function isHttpUrl(value) {
    return typeof value === "string" && /^https?:\/\//.test(value);
  }

  /** The http(s) url inside a `{url: …}` media object, else null. */
  function mediaUrl(media) {
    if (media && typeof media === "object" && isHttpUrl(media.url)) {
      return String(media.url);
    }
    return null;
  }

  /**
   * An int colour as `#rrggbb`, or null for anything else.
   *
   * Never a raw string: a colour reaches the DOM as a style *property* assignment, and
   * validating it to six hex digits first means even that cannot carry a payload.
   */
  function accentHex(color) {
    if (typeof color !== "number" || !Number.isInteger(color)) return null;
    return "#" + (color & 0xffffff).toString(16).padStart(6, "0");
  }

  // --- spec constructors ---------------------------------------------------------------
  //
  // A spec node is a plain object. Recognised fields:
  //
  //   tag       element name; absent means a bare text node
  //   cls       class attribute
  //   accent    validated `#rrggbb`, painted onto the left border
  //   url       href (on `a`) or src (on `img`); http(s)-validated before it is used
  //   alt       img alt text
  //   loading   img loading hint
  //   text      escaped text content
  //   md        raw markdown, rendered through cv2_model.renderMd (block-level)
  //   inline    raw markdown, rendered through cv2_model.inlineMd (no line handling)
  //   children  child specs
  //
  // Every `a` this walker emits points off-site, so target/rel are implied rather than
  // carried per node.

  const el = (tag, cls, extra) => Object.assign({ tag: tag, cls: cls }, extra || {});
  const text = (value) => ({ text: String(value) });

  /**
   * A child collection, as an array — `[]` for anything that is not one.
   *
   * `x || []` is not enough: this walker's input is Discord JSON captured from someone
   * else's server, and a `components` that arrives as a string or an object throws out
   * of `.map` rather than degrading. The Python renderer this replaced degraded, and
   * the builder can persist such a tree (its save path validates only "a list of
   * dicts"), where a throw wedges the canvas on every repaint.
   */
  const list = (value) => (Array.isArray(value) ? value : []);

  function placeholder(message) {
    return el("div", "cv2-placeholder", { text: "⚠️ " + message });
  }

  /**
   * Wrap a drawn node in its diff mark, if it carries one.
   *
   * Always OUTSIDE whatever the node draws as, so a departed button or accessory reads
   * as a whole thing gone rather than as one with red contents. Three callers reach a
   * node without going through `walk` — accessories and buttons are drawn by their
   * parent — so the rule lives here rather than in any one of them.
   */
  function marked(node, spec) {
    if (!spec || !node || !node._mark) return spec;
    return el("div", "cv2-" + node._mark, { children: [spec] });
  }

  function textBlock(content) {
    return el("div", "cv2-text", { md: String(content) });
  }

  // --- node walkers --------------------------------------------------------------------
  //
  // One function per Discord component kind, mirroring cv2_render.py's `_render_*`. A
  // walker returns null where the Python returns "" — the caller filters, so an
  // unrenderable node contributes nothing rather than an empty wrapper.

  /** Leading emoji for a button, from its `{name, id, animated}` object. */
  function emojiPrefix(emoji) {
    if (!emoji || typeof emoji !== "object") return [];
    const name = String(emoji.name || "");
    const id = emoji.id ? String(emoji.id) : "";
    if (id && /^\d+$/.test(id)) {
      const ext = emoji.animated ? "gif" : "png";
      return [
        el("img", "emoji", { url: EMOJI_CDN + id + "." + ext, alt: ":" + name + ":" }),
        text(" "),
      ];
    }
    return name ? [text(name + " ")] : [];
  }

  /**
   * A link button → an anchor button.
   *
   * Interactive buttons (styles 1–4, no url) are dropped rather than drawn inert: a
   * posted announcement has no interaction handler, so they never survive a real send
   * either, and the send whitelist drops them for the same reason.
   */
  function button(node) {
    if (!node || typeof node !== "object" || !isHttpUrl(node.url)) return null;
    return el("a", "cv2-button", {
      url: String(node.url),
      children: emojiPrefix(node.emoji).concat([text(node.label || "")]),
    });
  }

  function thumbnail(node) {
    const url = node && typeof node === "object" ? mediaUrl(node.media) : null;
    if (!url) return null;
    return el("img", "cv2-thumb" + (node.spoiler ? " cv2-spoiler" : ""), {
      url: url,
      // Discord's alt text is the only thing a screen reader has to go on for an
      // image-only post, so a described thumbnail keeps its description.
      alt: String(node.description || "thumbnail"),
    });
  }

  function accessory(node) {
    if (!node || typeof node !== "object") return null;
    // Discord allows exactly these two as a section accessory.
    const child =
      node.type === THUMBNAIL
        ? thumbnail(node)
        : node.type === BUTTON
          ? button(node)
          : null;
    if (!child) return null;
    return marked(node, el("div", "cv2-accessory", { children: [child] }));
  }

  function section(node) {
    const body = list(node.components).map(walk).filter(Boolean);
    // In a diff, `accessory` may be a LIST — the three-state case where one was swapped
    // for another, so both the old and the new are shown.
    const accs = Array.isArray(node.accessory)
      ? node.accessory.map(accessory).filter(Boolean)
      : [accessory(node.accessory)].filter(Boolean);
    // The body wrapper is emitted even when empty: it is what holds the text column
    // beside the accessory, and a section with no text still has to reserve it.
    return el("div", "cv2-section", {
      children: [el("div", "cv2-section-body", { children: body })].concat(accs),
    });
  }

  function media(node) {
    const items = [];
    for (const item of list(node.items)) {
      if (!item || typeof item !== "object") continue;
      const url = mediaUrl(item.media);
      if (url) {
        items.push({
          url: url,
          alt: String(item.description || ""),
          spoiler: !!item.spoiler,
        });
      }
    }
    if (!items.length) return null;
    const layout = MEDIA_LAYOUT[items.length] || "many";
    const tiles = items.map((it) =>
      el("a", "cv2-media-item" + (it.spoiler ? " cv2-spoiler" : ""), {
        url: it.url,
        children: [el("img", null, { url: it.url, alt: it.alt, loading: "lazy" })],
      }),
    );
    return el("div", "cv2-media " + layout, { children: tiles });
  }

  function separator(node) {
    // Discord's `spacing` is 1 (small) or 2 (large), and it applies to both forms. It
    // rides as a class rather than an inline style so cv2_preview.css stays the one
    // place the rendered post's appearance is described.
    const large = node.spacing === 2 ? " cv2-lg" : "";
    return node.divider === false
      ? el("div", "cv2-spacer" + large, { children: [] })
      : el("hr", "cv2-sep" + large);
  }

  function actionRow(node) {
    const buttons = list(node.components)
      .map((b) => marked(b, button(b)))
      .filter(Boolean);
    return buttons.length ? el("div", "cv2-buttons", { children: buttons }) : null;
  }

  function container(node) {
    return el("div", "cv2-container", {
      accent: accentHex(node.accent_color),
      children: list(node.components).map(walk).filter(Boolean),
    });
  }

  /**
   * A text leaf a diff has annotated: per-line equal / ins / del / replace.
   *
   * The rules differ per op and the difference is deliberate. An unchanged or inserted
   * line renders its markdown; a *removed* line renders as raw text, because dressing up
   * markup that no longer exists reads as though it still does. A replace-run is
   * word-level over raw text for the same reason — that is where an edit is legible.
   */
  function diffText(lines) {
    const parts = [];
    list(lines).forEach((entry) => {
      if (!entry || typeof entry !== "object") return;
      // Keyed on what was actually emitted, not on the index — a skipped entry must not
      // leave a blank line where a line never was.
      if (parts.length) parts.push(text("\n"));
      if (entry.op === "ins") {
        parts.push(el("ins", null, { line: String(entry.line || "") }));
      } else if (entry.op === "del") {
        parts.push(el("del", null, { text: String(entry.line || "") }));
      } else if (entry.op === "replace") {
        for (const run of list(entry.runs)) {
          if (!run || typeof run !== "object") continue;
          const body = text(String(run.text || ""));
          if (run.op === "del") parts.push(el("del", null, { children: [body] }));
          else if (run.op === "ins") parts.push(el("ins", null, { children: [body] }));
          else parts.push(body);
        }
      } else {
        // `equal`, and anything unrecognised. Drawing an unknown op as its plain line is
        // the safe default for a view whose whole job is being trustworthy about what
        // the text said: a missing arm used to fall into `replace` and, with no `runs`,
        // delete the line outright.
        parts.push({ line: String(entry.line || "") });
      }
    });
    return el("div", "cv2-text", { children: parts });
  }

  /**
   * A text leaf, diff-annotated or not.
   *
   * Its own function because both entry points reach it: `draw` for a text display
   * inside a tree, and `diffSpec` for a classic message's single content leaf, which
   * has no tree around it.
   */
  function textLeaf(node) {
    // An empty or unusable `_lines` falls back to `content` rather than drawing nothing:
    // the annotation is an *overlay* on a text leaf, and losing it must not lose the
    // text underneath it.
    if (list(node._lines).length) return diffText(node._lines);
    return textBlock(node.content === undefined ? "" : node.content);
  }

  /** One CV2 node → its spec, degrading an unknown kind to a labelled placeholder. */
  function walk(node) {
    if (!node || typeof node !== "object") return null;
    return marked(node, draw(node));
  }

  /** The undecorated draw of one node — `walk` adds any diff mark on top. */
  function draw(node) {
    switch (node.type) {
      case CONTAINER:
        return container(node);
      case TEXT_DISPLAY:
        return textLeaf(node);
      case SECTION:
        return section(node);
      case MEDIA_GALLERY:
        return media(node);
      case SEPARATOR:
        return separator(node);
      case THUMBNAIL:
        return thumbnail(node);
      case ACTION_ROW:
        return actionRow(node);
      case BUTTON:
        return button(node);
      case FILE:
        // A file component needs a real uploaded attachment, not a URL, so a captured
        // one can be named but never re-drawn.
        return placeholder("File attachment (from the original post)");
      default:
        return placeholder("Unsupported component (type " + node.type + ")");
    }
  }

  // --- classic (content + embeds) ------------------------------------------------------

  /**
   * A minimal embed card.
   *
   * Classic messages are the rare case (CV2 is the mirror-feed norm), so this keeps to
   * structure over pixel-fidelity — enough to see what the post said and how it was laid
   * out, not a facsimile of Discord's embed chrome.
   */
  function embed(data) {
    const parts = [];
    const author = data.author;
    if (author && typeof author === "object" && author.name) {
      parts.push(el("div", "embed-author", { text: String(author.name) }));
    }
    if (data.title) {
      parts.push(el("div", "embed-title", { inline: String(data.title) }));
    }
    if (data.description) {
      parts.push(
        el("div", "embed-desc", { children: [textBlock(data.description)] }),
      );
    }
    for (const field of list(data.fields)) {
      if (!field || typeof field !== "object") continue;
      const fp = [];
      if (field.name) {
        fp.push(el("div", "embed-field-name", { inline: String(field.name) }));
      }
      if (field.value) {
        fp.push(
          el("div", "embed-field-value", { children: [textBlock(field.value)] }),
        );
      }
      if (fp.length) parts.push(el("div", "embed-field", { children: fp }));
    }
    const imageUrl = mediaUrl(data.image) || mediaUrl(data.thumbnail);
    if (imageUrl) {
      parts.push(el("img", "embed-image", { url: imageUrl, alt: "embed image" }));
    }
    const footer = data.footer;
    if (footer && typeof footer === "object" && footer.text) {
      parts.push(el("div", "embed-footer", { text: String(footer.text) }));
    }
    return el("div", "cv2-embed", {
      accent: accentHex(data.color),
      children: parts,
    });
  }

  function classic(payload) {
    const content = String(payload.content || "");
    const embeds = list(payload.embeds).filter((e) => e && typeof e === "object");
    const bits = [];
    if (content.trim()) bits.push("text");
    if (embeds.length) bits.push(embeds.length + " embed(s)");
    const note = bits.join(" · ") || "empty message";
    const parts = [
      el("div", "cv2-note", { text: "Classic message — " + note }),
    ];
    if (content.trim()) parts.push(textBlock(content));
    for (const e of embeds) parts.push(embed(e));
    return el("div", "cv2-root classic", { children: parts });
  }

  // --- entry points --------------------------------------------------------------------

  /** A CV2 node list → the `.cv2-root` spec the preview surfaces draw. */
  function nodesSpec(nodes) {
    const body = list(nodes).map(walk).filter(Boolean);
    return body.length
      ? el("div", "cv2-root", { children: body })
      : placeholder("This version captured no renderable components.");
  }

  /**
   * A stored snapshot payload → its spec.
   *
   * `kind` selects the branch; an over-cap payload degrades to a note rather than
   * pretending it captured nothing.
   */
  function snapshotSpec(payload, kind) {
    if (!payload || typeof payload !== "object" || payload.truncated) {
      return placeholder(
        "This version's snapshot was too large to store in full.",
      );
    }
    if (kind === "cv2") return nodesSpec(payload.components || []);
    return classic(payload);
  }

  /**
   * A diff payload from the server → its spec.
   *
   * The alignment stays in Python (it needs difflib, and there is no zero-dependency
   * equal here); what arrives is the new tree with three annotations — `_mark` on a
   * whole node, `_lines` on a changed text leaf, and an `accessory` that may be a list.
   * Everything is pre-split, so the client only draws: no diffing happens in the
   * browser, on content that came from someone else's server.
   */
  function diffSpec(data) {
    if (!data || typeof data !== "object") return placeholder("No diff to show.");
    if (data.mode === "placeholder") return placeholder(data.message || "");

    const note = data.note
      ? el("div", "cv2-note", { text: data.note })
      : null;
    if (data.mode === "snapshot") {
      // The message changed format between versions, so the two are not comparable.
      return [note, snapshotSpec(data.payload, data.kind)].filter(Boolean);
    }

    // An annotated tree is still a tree: `walk` already handles `_mark`, and the text
    // arm already handles `_lines`, so the cv2 branch is the ordinary render.
    if (data.kind === "cv2") {
      return [note, nodesSpec(data.components)].filter(Boolean);
    }

    // Classic: the diffed content leaf, then the embeds. No "Classic message — …"
    // summary here; the diff is about what moved, not what the message is made of.
    const parts = [];
    if (data.content && typeof data.content === "object") {
      parts.push(textLeaf(data.content));
    }
    for (const e of list(data.embeds)) {
      if (e && typeof e === "object") parts.push(marked(e, embed(e)));
    }
    return [note, el("div", "cv2-root classic", { children: parts })].filter(Boolean);
  }

  // --- back end: html string -----------------------------------------------------------

  // Tags with no closing form. Matching the Python's output exactly matters: the golden
  // corpus compares the two byte for byte.
  const VOID = { img: true, hr: true };

  /**
   * The tag a spec actually renders as.
   *
   * Under `inert`, an anchor becomes a span. The builder canvas needs that: the canvas
   * IS the editing surface, so clicking a link button or a gallery tile has to select
   * the block, not navigate away from the draft. Everywhere else — the mirror log, the
   * publish confirmation — the links are real, because there the post is something you
   * read rather than something you are holding.
   */
  function tagOf(spec, opts) {
    return opts.inert && spec.tag === "a" ? "span" : spec.tag;
  }

  // Attribute order is fixed so the two renderers' output is comparable as text, not
  // just as a DOM.
  function attrs(spec, opts) {
    const out = [];
    const push = (name, value) => out.push(" " + name + '="' + M.esc(value) + '"');
    if (spec.cls) push("class", spec.cls);
    if (spec.accent) push("style", "border-left-color:" + spec.accent);
    // Re-checked here rather than trusted from the walker: this is the only place a URL
    // becomes an attribute, so it is the only place the check has to hold.
    if (spec.url && isHttpUrl(spec.url)) {
      if (spec.tag === "a") {
        if (!opts.inert) {
          push("href", spec.url);
          push("target", "_blank");
          push("rel", "noopener noreferrer");
        }
      } else {
        push("src", spec.url);
      }
    }
    if (spec.alt !== undefined) push("alt", spec.alt);
    if (spec.loading) push("loading", spec.loading);
    return out.join("");
  }

  function inner(spec, opts) {
    if (spec.md !== undefined) return M.renderMd(spec.md, opts.emoji, opts.now);
    if (spec.inline !== undefined) {
      return M.inlineMd(spec.inline, opts.emoji, opts.now);
    }
    // One line, without the block-level heading-spacing pass — a diff has already
    // decided where the lines fall, so re-flowing them would fight it.
    if (spec.line !== undefined) return M.lineMd(spec.line, opts.emoji, opts.now);
    if (spec.children) return serialize(spec.children, opts);
    if (spec.text !== undefined) return M.esc(spec.text);
    return "";
  }

  /**
   * Just the opening tag of a spec, for a host that supplies its own children.
   *
   * The builder canvas needs this for containers and sections: it takes the wrapper —
   * classes, validated accent — from here so the card looks like the real post, but it
   * has to interleave insert rails between the children and wrap each one in its own
   * selectable block, which is editor chrome the shared renderer knows nothing about.
   */
  function openTag(spec, opts) {
    opts = opts || {};
    return "<" + tagOf(spec, opts) + attrs(spec, opts) + ">";
  }

  /** A spec (or list of specs) → an HTML string. Pure — no DOM required. */
  function serialize(spec, opts) {
    opts = opts || {};
    if (spec === null || spec === undefined) return "";
    if (Array.isArray(spec)) {
      return spec.map((s) => serialize(s, opts)).join("");
    }
    // A tagless spec is either escaped text or a bare markdown fragment — a diff's
    // unchanged lines sit directly in the text block, with no wrapper of their own.
    // `inner` already dispatches on exactly those fields, so it does the job here too.
    if (!spec.tag) return inner(spec, opts);
    const tag = tagOf(spec, opts);
    const open = "<" + tag + attrs(spec, opts) + ">";
    if (VOID[tag]) return open;
    return open + inner(spec, opts) + "</" + tag + ">";
  }

  // --- back end: real DOM --------------------------------------------------------------

  /**
   * A spec (or list of specs) → DOM. Browser only.
   *
   * This is the chokepoint the whole design turns on. Text goes to `textContent`, where
   * an injection cannot be expressed; a URL is checked before it becomes an attribute;
   * a colour is assigned as a style *property*, which cannot escape into a declaration.
   * `md`/`inline` are the sole innerHTML sinks and take only renderMd/inlineMd output.
   */
  function materialize(spec, opts, doc) {
    opts = opts || {};
    doc = doc || document;
    if (spec === null || spec === undefined) return doc.createDocumentFragment();
    if (Array.isArray(spec)) {
      const frag = doc.createDocumentFragment();
      for (const s of spec) frag.appendChild(materialize(s, opts, doc));
      return frag;
    }
    if (!spec.tag) {
      // The same field dispatch serialize()'s tagless branch does, or the two back ends
      // disagree about a spec no walker happens to emit today — and `el`/`text` are
      // exported so a host CAN assemble one. A markdown fragment goes through a template
      // rather than an added wrapper element so the DOM matches what serialize() writes
      // exactly, and it is the same escape-by-construction output `md` is, not raw input.
      if (
        spec.md !== undefined ||
        spec.inline !== undefined ||
        spec.line !== undefined
      ) {
        const tpl = doc.createElement("template");
        tpl.innerHTML = inner(spec, opts);
        return tpl.content;
      }
      if (spec.children) return materialize(spec.children, opts, doc);
      return doc.createTextNode(spec.text === undefined ? "" : spec.text);
    }

    const node = doc.createElement(tagOf(spec, opts));
    if (spec.cls) node.className = spec.cls;
    if (spec.accent) node.style.borderLeftColor = spec.accent;
    if (spec.url && isHttpUrl(spec.url)) {
      if (spec.tag === "a") {
        if (!opts.inert) {
          node.setAttribute("href", spec.url);
          node.setAttribute("target", "_blank");
          node.setAttribute("rel", "noopener noreferrer");
        }
      } else {
        node.setAttribute("src", spec.url);
      }
    }
    if (spec.alt !== undefined) node.setAttribute("alt", spec.alt);
    if (spec.loading) node.setAttribute("loading", spec.loading);

    if (spec.md !== undefined) {
      node.innerHTML = M.renderMd(spec.md, opts.emoji, opts.now);
    } else if (spec.inline !== undefined) {
      node.innerHTML = M.inlineMd(spec.inline, opts.emoji, opts.now);
    } else if (spec.line !== undefined) {
      node.innerHTML = M.lineMd(spec.line, opts.emoji, opts.now);
    } else if (spec.children) {
      for (const child of spec.children) {
        node.appendChild(materialize(child, opts, doc));
      }
    } else if (spec.text !== undefined) {
      node.textContent = spec.text;
    }
    return node;
  }

  /** Draw a spec into `host`, replacing whatever was there. Browser only. */
  function render(host, spec, opts) {
    host.replaceChildren(materialize(spec, opts, host.ownerDocument));
  }

  // Only what something outside this file actually calls. `classic`/`embed` are
  // reachable through snapshotSpec and diffSpec; the rest of the leaf walkers and spec
  // helpers are internal, and exporting them would invite a caller to assemble a post
  // out of parts rather than from a node tree.
  const CV2Render = {
    // walkers
    walk,
    nodesSpec,
    snapshotSpec,
    diffSpec,
    // back ends
    serialize,
    openTag,
    materialize,
    render,
    // the pieces the builder needs to dress its own chrome as the real thing
    accentHex,
    emojiPrefix,
    el,
    text,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = CV2Render;
  if (typeof window !== "undefined") window.CV2Render = CV2Render;
})();
