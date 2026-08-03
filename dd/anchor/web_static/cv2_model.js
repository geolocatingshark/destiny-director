// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// Pure Components-V2 node model for the web builder — the client-side mirror of
// dd/anchor/cv2_nodes.py. A "node" is a raw Discord component-payload dict (the exact
// JSON the REST API accepts); the builder holds an ordered array of top-level nodes and
// mutates it through the helpers here.
//
// This file has NO DOM access and NO module-level mutable state — every function takes
// the node list it operates on, exactly like cv2_nodes.py does. That is what makes it
// unit-testable under `node --test` (see tests/cv2_model.test.js, run by `make test-js`).
// The UI layer lives in cv2_builder.js.
//
// Keep this in lockstep with cv2_nodes.py. The server re-sanitizes (sanitize_for_preview)
// and re-validates (validate) every node list on publish, so a client/server drift shows
// up as a preview that differs from the sent post — never as an invalid post reaching
// Discord.
//
// Paths. A path is an array addressing one node from the root list: [0, 2] is the third
// child of the first top-level node. The single non-integer segment is the string "acc",
// addressing a section's accessory (which is a named field, not a child index).
//
// Runs as a classic browser script (attaches window.CV2Model) and as a CommonJS module
// (module.exports) so the same file is importable from node:test.

(function () {
  "use strict";

  // --- Discord component type ids (mirror cv2_nodes) --------------------------------
  const ACTION_ROW = 1;
  const BUTTON = 2;
  const SECTION = 9;
  const TEXT_DISPLAY = 10;
  const THUMBNAIL = 11;
  const MEDIA_GALLERY = 12;
  const FILE = 13;
  const SEPARATOR = 14;
  const CONTAINER = 17;

  const LINK_BUTTON_STYLE = 5; // hikari.ButtonStyle.LINK
  const MAX_GALLERY_ITEMS = 10;
  const MAX_SECTION_TEXTS = 3;
  const MAX_TOP_LEVEL = 10;

  const KIND_BY_TYPE = {
    [CONTAINER]: "container",
    [TEXT_DISPLAY]: "text",
    [SECTION]: "section",
    [MEDIA_GALLERY]: "media",
    [SEPARATOR]: "separator",
    [FILE]: "file",
    [THUMBNAIL]: "thumbnail",
    [ACTION_ROW]: "link_button",
    [BUTTON]: "link_button",
  };

  // Human labels, mirroring cv2_nodes.ADD_LABELS.
  const KIND_LABEL = {
    container: "Container",
    text: "Text",
    section: "Section",
    media: "Image gallery",
    separator: "Separator",
    link_button: "Link button",
    thumbnail: "Thumbnail",
    file: "File",
  };

  /** Classify a node into a builder "kind" (cv2_nodes.kind). */
  function kind(node) {
    return (node && KIND_BY_TYPE[node.type]) || "unknown";
  }

  /** The button dict inside a link-button node, unwrapping the action row. */
  function buttonOf(node) {
    return node.type === ACTION_ROW ? node.components[0] : node;
  }

  // --- constructors -----------------------------------------------------------------
  // `defaultAccent` mirrors cv2_nodes.make_container seeding cfg.embed_default_color, so
  // a container made here matches one made by /post components. The host passes the
  // server's value; omitting it yields Discord's neutral bar.
  function makeContainer(defaultAccent) {
    const node = { type: CONTAINER, components: [] };
    if (Number.isInteger(defaultAccent)) node.accent_color = defaultAccent;
    return node;
  }
  function makeText(content) {
    return { type: TEXT_DISPLAY, content: content || "" };
  }
  // A fresh section starts with one empty text block: an accessory-only section is
  // invalid, and starting empty makes the very first thing you see a validation error.
  function makeSection() {
    return { type: SECTION, components: [makeText("")] };
  }
  function makeMediaGallery() {
    return { type: MEDIA_GALLERY, items: [] };
  }
  function makeSeparator() {
    return { type: SEPARATOR, divider: true, spacing: 1 };
  }
  function makeThumbnail() {
    return { type: THUMBNAIL, media: { url: "" } };
  }
  /** A bare link button, as used for a section accessory. */
  function makeButton() {
    return { type: BUTTON, style: LINK_BUTTON_STYLE, label: "", url: "" };
  }
  /** A link button wrapped in its own action row (buttons can't be loose children). */
  function makeLinkButton() {
    return { type: ACTION_ROW, components: [makeButton()] };
  }

  function makeNode(k, defaultAccent) {
    switch (k) {
      case "container":
        return makeContainer(defaultAccent);
      case "text":
        return makeText("");
      case "section":
        return makeSection();
      case "media":
        return makeMediaGallery();
      case "separator":
        return makeSeparator();
      case "link_button":
        return makeLinkButton();
      case "thumbnail":
        return makeThumbnail();
      default:
        throw new Error("Unknown node kind: " + k);
    }
  }

  // --- path helpers -----------------------------------------------------------------

  function samePath(a, b) {
    return !!a && !!b && a.length === b.length && a.every((v, i) => v === b[i]);
  }

  /** Whether `p` is `q` itself or one of its ancestors. */
  function isPrefix(p, q) {
    return p.length <= q.length && p.every((v, i) => v === q[i]);
  }

  /** The node at `path` (cv2_nodes.resolve_path). */
  function resolve(nodes, path) {
    let node = { type: CONTAINER, components: nodes };
    for (const seg of path) {
      node = seg === "acc" ? node.accessory : node.components[seg];
    }
    return node;
  }

  /**
   * The *mutable* child list of the container/section at `scope` (the root list when
   * `scope` is empty). Returns the real array reference even when empty, so callers can
   * splice into it — mirroring cv2_nodes.scope_children's explicit `is None` check.
   */
  function childList(nodes, scope) {
    if (!scope.length) return nodes;
    const node = resolve(nodes, scope);
    if (!node.components) node.components = [];
    return node.components;
  }

  function scopeKind(nodes, scope) {
    return scope.length ? kind(resolve(nodes, scope)) : "root";
  }

  /**
   * Rebase a path captured *before* a removal.
   *
   * Splicing a node out shifts every later sibling down one, and therefore every path
   * that descends through one. Without this, "drag a top-level block into a container
   * that sits below it" resolves to the wrong node (or throws): the container's own
   * index moved while we were holding it.
   */
  function adjustAfterRemoval(path, removed) {
    if (!path) return path;
    const scope = removed.slice(0, -1);
    const idx = removed[removed.length - 1];
    if (idx === "acc") return path; // an accessory is a field, not an index
    if (path.length > scope.length && isPrefix(scope, path) && path[scope.length] > idx) {
      const out = path.slice();
      out[scope.length] -= 1;
      return out;
    }
    return path;
  }

  // --- nesting rules (mirror cv2_nodes.addable_kinds) --------------------------------

  /** The kinds that may be inserted directly into `scope`. */
  function allowedIn(nodes, scope) {
    const sk = scopeKind(nodes, scope);
    // A section holds text displays plus one accessory; the accessory is set through
    // its own slot, not by inserting into the child list, so it isn't listed here.
    if (sk === "section") return ["text"];
    const base = ["text", "section", "media", "separator", "link_button"];
    // Containers are top-level only — they cannot nest.
    if (sk === "root") base.unshift("container");
    return base;
  }

  /**
   * Why `k` may not go into `scope`, in the author's words.
   *
   * The in-Discord builder expressed the nesting rules by *omitting* options from a
   * dropdown, so a rule you tripped over was invisible. On the web there is room to say
   * it, which turns a dead end into an explanation.
   */
  function refusalReason(nodes, scope, k) {
    const sk = scopeKind(nodes, scope);
    if (sk === "section") {
      if (k === "thumbnail" || k === "link_button") {
        return "Drop it on the section's accessory slot instead.";
      }
      if (k === "text" && childList(nodes, scope).length >= MAX_SECTION_TEXTS) {
        return "A section holds at most " + MAX_SECTION_TEXTS + " text blocks.";
      }
      return "A section holds text blocks and one accessory — nothing else.";
    }
    if (k === "container") return "Containers are top level only — they can't nest.";
    if (k === "thumbnail") return "A thumbnail is only ever a section's accessory.";
    return "That block can't go there.";
  }

  /**
   * Whether a node of kind `k` may be dropped into `scope`.
   *
   * `movingFrom` is the path of an existing node being dragged (null when adding a new
   * one), and gates two cases the kind alone can't: dropping a node into itself or its
   * own descendants, and reordering within an already-full section (which adds nothing).
   */
  function canDrop(nodes, scope, k, movingFrom) {
    if (movingFrom && isPrefix(movingFrom, scope)) return false;
    if (
      scopeKind(nodes, scope) === "section" &&
      childList(nodes, scope).length >= MAX_SECTION_TEXTS &&
      !(movingFrom && samePath(movingFrom.slice(0, -1), scope))
    ) {
      return false;
    }
    return allowedIn(nodes, scope).indexOf(k) !== -1;
  }

  /** Whether this kind can be a section accessory. */
  function isAccessoryKind(k) {
    return k === "thumbnail" || k === "link_button";
  }

  // --- mutations --------------------------------------------------------------------
  // Each returns the path that should now be selected, so the caller never has to
  // recompute one from a tree it just reshaped.

  function insertAt(nodes, scope, index, node) {
    childList(nodes, scope).splice(index, 0, node);
    return scope.concat([index]);
  }

  /** Remove the node at `path`; returns the path to select next (or null). */
  function removeAt(nodes, path) {
    const scope = path.slice(0, -1);
    const last = path[path.length - 1];
    if (last === "acc") {
      delete resolve(nodes, scope).accessory;
      return scope;
    }
    childList(nodes, scope).splice(last, 1);
    const list = childList(nodes, scope);
    if (list.length) return scope.concat([Math.min(last, list.length - 1)]);
    return scope.length ? scope : null;
  }

  /** Move the node at `from` into `toScope` at `toIndex`; returns its new path. */
  function moveNode(nodes, from, toScope, toIndex) {
    const node = JSON.parse(JSON.stringify(resolve(nodes, from)));
    const fromScope = from.slice(0, -1);
    const fromIdx = from[from.length - 1];
    let scope = toScope;
    let index = toIndex;
    if (fromIdx === "acc") {
      delete resolve(nodes, fromScope).accessory;
    } else {
      childList(nodes, fromScope).splice(fromIdx, 1);
      // Removing an earlier sibling in the same scope shifts the target left...
      if (samePath(fromScope, toScope) && fromIdx < index) index -= 1;
      // ...and removing anything shifts paths that descend past it (no-op when equal).
      scope = adjustAfterRemoval(scope, from);
    }
    return insertAt(nodes, scope, index, node);
  }

  /** Set a section's accessory. A section accessory is a BARE button, never a row. */
  function setAccessory(nodes, sectionPath, accessory) {
    const node = accessory.type === ACTION_ROW ? accessory.components[0] : accessory;
    resolve(nodes, sectionPath).accessory = node;
    return sectionPath.concat(["acc"]);
  }

  // --- validation (mirror cv2_nodes.validate) ---------------------------------------
  // Same messages as the Python, but each problem carries the PATH of the node that
  // caused it, so the UI can select and scroll to the offender instead of printing a
  // wall of prose the way the in-Discord builder had to.

  function validate(nodes) {
    const problems = [];
    const push = (path, msg) => problems.push({ path: path, msg: msg });

    if (!nodes.length) push(null, "The message is empty — add at least one block.");
    if (nodes.length > MAX_TOP_LEVEL) {
      push(
        null,
        "Too many top-level blocks (" +
          nodes.length +
          "); Discord allows " +
          MAX_TOP_LEVEL +
          ". Group some inside a container.",
      );
    }

    (function walk(list, base) {
      list.forEach((node, i) => {
        const path = base.concat([i]);
        const k = kind(node);
        if (k === "container") {
          const children = node.components || [];
          if (!children.length) {
            push(path, "A container is empty — add a block inside or delete it.");
          }
          walk(children, path);
        } else if (k === "section") {
          const texts = node.components || [];
          if (texts.length < 1 || texts.length > MAX_SECTION_TEXTS) {
            push(
              path,
              "A section must have 1–" +
                MAX_SECTION_TEXTS +
                " text blocks (it has " +
                texts.length +
                ").",
            );
          }
          if (!node.accessory) {
            push(path, "A section is missing its accessory (thumbnail or button).");
          } else if (
            kind(node.accessory) === "thumbnail" &&
            !(node.accessory.media || {}).url
          ) {
            push(path.concat(["acc"]), "The section's thumbnail has no image URL.");
          } else if (kind(node.accessory) === "link_button") {
            const b = buttonOf(node.accessory);
            if (!(b.label && b.url)) {
              push(
                path.concat(["acc"]),
                "The section's button needs both a label and a URL.",
              );
            }
          }
          walk(texts, path);
        } else if (k === "text") {
          if (!String(node.content || "").trim()) push(path, "A text block is empty.");
        } else if (k === "media") {
          if (!(node.items || []).length) push(path, "A media gallery has no images.");
        } else if (k === "link_button") {
          const b = buttonOf(node);
          if (!(b.label && b.url)) {
            push(path, "A link button needs both a label and a URL.");
          }
        }
      });
    })(nodes, []);

    return problems;
  }

  // --- markdown (a client mirror of hybrid_post_core._render_line) -------------------
  // Renders the *canvas*, which is the live editing surface, so it cannot be a server
  // round-trip. cv2_html.render_cv2_nodes_html stays the authoritative render (shown in
  // the publish confirmation) and the server re-sanitizes on publish regardless.
  //
  // Everything is escaped first and only http(s) links become anchors, so this holds the
  // same line the server does even though the author is rendering their own text.

  function esc(s) {
    return String(s).replace(
      /[&<>"]/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
    );
  }

  /**
   * Inline markdown. `emoji` is an optional {name: url} map for `:shortcode:`
   * substitution — the client mirror of hybrid_post_core._html_emoji_substituter. An
   * unknown shortcode stays as escaped text, exactly as the server leaves it.
   */
  function inlineMd(s, emoji) {
    let out = esc(s);
    out = out.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    if (emoji) {
      out = out.replace(/:(\w+):/g, (whole, name) => {
        const url = emoji[name] || emoji[name.toLowerCase()];
        if (!url) return whole;
        return (
          '<img class="emoji" src="' + esc(url) + '" alt=":' + esc(name) + ':">'
        );
      });
    }
    return out;
  }

  function lineMd(line, emoji) {
    if (line.startsWith("-# ")) {
      return '<span class="md-small">' + inlineMd(line.slice(3), emoji) + "</span>";
    }
    if (line.startsWith("### ")) {
      return '<span class="md-h3">' + inlineMd(line.slice(4), emoji) + "</span>";
    }
    if (line.startsWith("## ")) {
      return '<span class="md-h2">' + inlineMd(line.slice(3), emoji) + "</span>";
    }
    if (line.startsWith("# ")) {
      return '<span class="md-h1">' + inlineMd(line.slice(2), emoji) + "</span>";
    }
    if (/^[-*] /.test(line)) {
      return '<span class="md-bullet">' + inlineMd(line.slice(2), emoji) + "</span>";
    }
    return inlineMd(line, emoji);
  }

  /** Render a text leaf's content. Newlines survive via the .cv2-text pre-wrap. */
  function renderMd(content, emoji) {
    return String(content)
      .split("\n")
      .map((line) => lineMd(line, emoji))
      .join("\n");
  }

  // --- exports ----------------------------------------------------------------------

  const CV2Model = {
    // type ids
    ACTION_ROW,
    BUTTON,
    SECTION,
    TEXT_DISPLAY,
    THUMBNAIL,
    MEDIA_GALLERY,
    FILE,
    SEPARATOR,
    CONTAINER,
    LINK_BUTTON_STYLE,
    // limits
    MAX_GALLERY_ITEMS,
    MAX_SECTION_TEXTS,
    MAX_TOP_LEVEL,
    // labels
    KIND_LABEL,
    // classification
    kind,
    buttonOf,
    // constructors
    makeContainer,
    makeText,
    makeSection,
    makeMediaGallery,
    makeSeparator,
    makeThumbnail,
    makeButton,
    makeLinkButton,
    makeNode,
    // paths
    samePath,
    isPrefix,
    resolve,
    childList,
    scopeKind,
    adjustAfterRemoval,
    // rules
    allowedIn,
    refusalReason,
    canDrop,
    isAccessoryKind,
    // mutations
    insertAt,
    removeAt,
    moveNode,
    setAccessory,
    // validation
    validate,
    // markdown
    esc,
    inlineMd,
    lineMd,
    renderMd,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = CV2Model;
  if (typeof window !== "undefined") window.CV2Model = CV2Model;
})();
