// Per-feed actions page — the post this feed would produce, and the three things you
// can do with it. Served by dd.anchor.extensions.feed_page.
//
// The shell is static (the CSP is script-src 'self', so no inline bootstrap): the feed
// name comes from the URL, and everything else from GET /feed/<name>/data.
//
// The preview is drawn by the SHARED renderer (cv2_render.js), from the node tree the
// server returns — the same {kind, payload, message_kind} shape /mirror-logs/render
// serves, so both preview surfaces draw through one renderer rather than two.

(() => {
  // /feed/<name> — decodeURIComponent because a followable name is path-encoded above.
  const NAME = decodeURIComponent(window.location.pathname.split("/")[2] || "");

  const byId = (id) => document.getElementById(id);
  const heading = byId("heading");
  const previewBtn = byId("previewBtn");
  const sendBtn = byId("sendBtn");
  const status = byId("status");
  const previewBox = byId("previewBox");
  const dialog = byId("sendDialog");
  const dialogBody = byId("dialogBody");
  const publish = byId("publish");
  const confirmSend = byId("confirmSend");
  const cancelSend = byId("cancelSend");

  let feed = null;

  function say(message, isError) {
    status.classList.toggle("err", !!isError);
    status.textContent = message;
  }

  async function loadFeed() {
    const res = await fetch("/feed/" + encodeURIComponent(NAME) + "/data");
    if (!res.ok) throw new Error("Could not load this feed.");
    feed = await res.json();

    heading.textContent = feed.title;
    document.title = feed.title + " — Destiny Director";

    // A dormant feed has no configured followable channel: it still previews
    // (construction needs no channel), but there is nowhere to send it.
    if (feed.dormant) {
      const note = byId("dormantNote");
      note.hidden = false;
      note.textContent =
        "Dormant — no '" + feed.name + "' entry in FOLLOWABLES, so there is no " +
        "channel to post to. Preview still works. Add the followable channel id " +
        "to enable sending.";
      sendBtn.title = "This feed is dormant — no channel is configured to post to.";
    } else {
      sendBtn.disabled = false;
    }
    previewBtn.disabled = false;
  }

  previewBtn.addEventListener("click", async () => {
    previewBtn.disabled = true;
    say("Building…", false);
    try {
      const res = await fetch("/feed/" + encodeURIComponent(NAME) + "/preview");
      const data = await res.json();
      if (data.error) {
        // A build failure is a legitimate answer — Iron Banner between events raises,
        // and the Discord `show` reported it the same way.
        previewBox.replaceChildren();
        say(data.error, true);
      } else {
        window.CV2Render.render(
          previewBox,
          window.CV2Render.snapshotSpec(data.payload, data.message_kind),
          {},
        );
        say("", false);
      }
    } catch (e) {
      say("Render error: " + e, true);
    } finally {
      previewBtn.disabled = false;
    }
  });

  sendBtn.addEventListener("click", () => {
    const where = feed.channelId ? "#" + feed.channelId : "its channel";
    dialogBody.textContent =
      "This posts the " + feed.title + " post to " + where + " straight away. " +
      "It cannot be recalled, only edited or deleted afterwards.";
    publish.checked = true;
    dialog.showModal();
  });

  cancelSend.addEventListener("click", () => dialog.close());

  confirmSend.addEventListener("click", async () => {
    const wantPublish = publish.checked;
    dialog.close();
    sendBtn.disabled = true;
    say("Building…", false);
    try {
      const res = await window.api("/feed/" + encodeURIComponent(NAME) + "/send", {
        publish: wantPublish,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        say("Send started — check Mirror logs for delivery.", false);
      } else {
        say(data.error || "Send failed.", true);
      }
    } catch (_) {
      say("Network error — try again.", true);
    } finally {
      sendBtn.disabled = !!(feed && feed.dormant);
    }
  });

  loadFeed().catch((e) => say(String(e), true));
})();
