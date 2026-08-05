// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// Host page for the CV2 builder widget. Everything editor-shaped lives in
// cv2_builder.js; this file only does the server round-trips for one draft:
// load seed -> mount -> autosave -> confirm-render -> publish.

(function () {
  "use strict";

  // /cv2-builder/<draft id>
  const DRAFT_ID = window.location.pathname.split("/").filter(Boolean).pop();
  const BASE = "/cv2-builder/" + encodeURIComponent(DRAFT_ID);

  // Copy per publish action, so the page says what will actually happen rather than a
  // generic "Save". Kyber sees one sentence, not a mode.
  const ACTIONS = {
    post: {
      button: "Post",
      title: "New message",
      subtitle: (d) =>
        d.target_channel_mention
          ? "This will be posted to " + d.target_channel_mention + "."
          : "This will be posted to the channel you ran the command in.",
    },
    edit: {
      button: "Save changes",
      title: "Edit message",
      subtitle: () => "Editing a message already posted. Saving replaces it in place.",
    },
    copy: {
      button: "Send copy",
      title: "Copy message",
      subtitle: (d) =>
        d.target_channel_mention
          ? "A copy will be sent to " + d.target_channel_mention + "."
          : "A copy will be sent to the channel you ran the command in.",
    },
  };

  async function api(path, options) {
    const response = await fetch(BASE + path, options);
    if (!response.ok) {
      let detail = "";
      try {
        detail = (await response.json()).error || "";
      } catch (e) {
        detail = await response.text();
      }
      throw new Error(detail || "Request failed (" + response.status + ")");
    }
    return response;
  }

  const postJson = (path, body) =>
    api(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });

  function fail(message) {
    const box = document.getElementById("loadError");
    box.textContent = message;
    box.classList.remove("hidden");
    document.getElementById("pageSubtitle").textContent = "";
  }

  async function main() {
    let draft;
    try {
      draft = await (await api("/data")).json();
    } catch (e) {
      fail(
        "Couldn't load this draft. It may have expired, or it belongs to a different " +
          "account. Run the command in Discord again to start a new one.",
      );
      return;
    }

    const action = ACTIONS[draft.action] || ACTIONS.post;
    document.getElementById("pageTitle").textContent = action.title;
    document.getElementById("pageSubtitle").textContent = action.subtitle(draft);

    if (draft.published_message_link) {
      document.getElementById("pageSubtitle").innerHTML =
        'Already sent — <a href="' +
        draft.published_message_link +
        '" target="_blank" rel="noopener noreferrer">open in Discord</a>. ' +
        "Editing here and sending again will post a second message.";
    }

    window.initCv2Builder(document.getElementById("mount"), {
      nodes: draft.nodes || [],
      emoji: draft.emoji || {},
      defaultAccent: draft.default_accent,
      actionLabel: action.button,
      onSave: (nodes) => postJson("/save", { nodes: nodes }),
      // Returns {nodes, problems} — the sanitized tree the server would post, which the
      // widget renders itself. It used to return HTML, back when the server render was
      // a second implementation worth confirming against; now one renderer draws every
      // surface and the authority here is the data.
      onPreview: async (nodes) =>
        await (await postJson("/preview", { nodes: nodes })).json(),
      onPublish: async (nodes) =>
        (await (await postJson("/publish", { nodes: nodes })).json()),
    });
  }

  main();
})();
