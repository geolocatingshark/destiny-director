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
// the one that survives. See plans/preview_renderer_unification.md.
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

  function placeholder(message) {
    return el("div", "cv2-placeholder", { text: "⚠️ " + message });
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
    const inner = node.type === THUMBNAIL ? thumbnail(node) : null;
    const btn = node.type === BUTTON ? button(node) : null;
    const child = inner || btn;
    return child ? el("div", "cv2-accessory", { children: [child] }) : null;
  }

  function section(node) {
    const body = (node.components || []).map(walk).filter(Boolean);
    const acc = accessory(node.accessory);
    // The body wrapper is emitted even when empty: it is what holds the text column
    // beside the accessory, and a section with no text still has to reserve it.
    const children = [el("div", "cv2-section-body", { children: body })];
    if (acc) children.push(acc);
    return el("div", "cv2-section", { children: children });
  }

  function media(node) {
    const items = [];
    for (const item of node.items || []) {
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
    return node.divider === false
      ? el("div", "cv2-spacer", { children: [] })
      : el("hr", "cv2-sep");
  }

  function actionRow(node) {
    const buttons = (node.components || []).map(button).filter(Boolean);
    return buttons.length ? el("div", "cv2-buttons", { children: buttons }) : null;
  }

  function container(node) {
    return el("div", "cv2-container", {
      accent: accentHex(node.accent_color),
      children: (node.components || []).map(walk).filter(Boolean),
    });
  }

  /** One CV2 node → its spec, degrading an unknown kind to a labelled placeholder. */
  function walk(node) {
    if (!node || typeof node !== "object") return null;
    switch (node.type) {
      case CONTAINER:
        return container(node);
      case TEXT_DISPLAY:
        return textBlock(node.content === undefined ? "" : node.content);
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
    for (const field of data.fields || []) {
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
    const embeds = (payload.embeds || []).filter(
      (e) => e && typeof e === "object",
    );
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
    const body = (nodes || []).map(walk).filter(Boolean);
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

  // --- back end: html string -----------------------------------------------------------

  // Tags with no closing form. Matching the Python's output exactly matters: the golden
  // corpus compares the two byte for byte.
  const VOID = { img: true, hr: true };

  // Attribute order is fixed so the two renderers' output is comparable as text, not
  // just as a DOM.
  function attrs(spec) {
    const out = [];
    const push = (name, value) => out.push(" " + name + '="' + M.esc(value) + '"');
    if (spec.cls) push("class", spec.cls);
    if (spec.accent) push("style", "border-left-color:" + spec.accent);
    // Re-checked here rather than trusted from the walker: this is the only place a URL
    // becomes an attribute, so it is the only place the check has to hold.
    if (spec.url && isHttpUrl(spec.url)) {
      if (spec.tag === "a") {
        push("href", spec.url);
        push("target", "_blank");
        push("rel", "noopener noreferrer");
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
    if (spec.children) return serialize(spec.children, opts);
    if (spec.text !== undefined) return M.esc(spec.text);
    return "";
  }

  /** A spec (or list of specs) → an HTML string. Pure — no DOM required. */
  function serialize(spec, opts) {
    opts = opts || {};
    if (spec === null || spec === undefined) return "";
    if (Array.isArray(spec)) {
      return spec.map((s) => serialize(s, opts)).join("");
    }
    if (!spec.tag) return M.esc(spec.text === undefined ? "" : spec.text);
    const open = "<" + spec.tag + attrs(spec) + ">";
    if (VOID[spec.tag]) return open;
    return open + inner(spec, opts) + "</" + spec.tag + ">";
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
      return doc.createTextNode(spec.text === undefined ? "" : spec.text);
    }

    const node = doc.createElement(spec.tag);
    if (spec.cls) node.className = spec.cls;
    if (spec.accent) node.style.borderLeftColor = spec.accent;
    if (spec.url && isHttpUrl(spec.url)) {
      if (spec.tag === "a") {
        node.setAttribute("href", spec.url);
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
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

  const CV2Render = {
    // walkers
    walk,
    nodesSpec,
    snapshotSpec,
    classic,
    embed,
    // back ends
    serialize,
    materialize,
    render,
    // shared predicates, exported so the builder applies the same rules to its chrome
    isHttpUrl,
    mediaUrl,
    accentHex,
    // spec helpers
    el,
    text,
    placeholder,
    textBlock,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = CV2Render;
  if (typeof window !== "undefined") window.CV2Render = CV2Render;
})();
