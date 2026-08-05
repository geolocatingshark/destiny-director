// Per-feed actions page — preview the post the producer would build right now, and
// send it to the feed's channel. Served by dd.anchor.extensions.feed_page.
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
  const previewStatus = byId("previewStatus");
  const previewBox = byId("previewBox");
  const sendBtn = byId("sendBtn");
  const publish = byId("publish");
  const sendStatus = byId("sendStatus");

  let feed = null;

  function fail(el, message) {
    el.classList.add("err");
    el.textContent = message;
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
      byId("dormantPanel").hidden = false;
      byId("dormantNote").textContent =
        "Dormant — no '" + feed.name + "' entry in FOLLOWABLES, so there is no " +
        "channel to post to. Preview still works. Add the followable channel id " +
        "to enable sending.";
      sendBtn.disabled = true;
      publish.disabled = true;
    } else {
      sendBtn.disabled = false;
    }
    previewBtn.disabled = false;
  }

  previewBtn.addEventListener("click", async () => {
    previewBtn.disabled = true;
    previewStatus.classList.remove("err");
    previewStatus.textContent = "Building…";
    try {
      const res = await fetch("/feed/" + encodeURIComponent(NAME) + "/preview");
      const data = await res.json();
      if (data.error) {
        // A build failure is a legitimate answer — Iron Banner between events raises,
        // and the Discord `show` reported it the same way.
        previewBox.replaceChildren();
        fail(previewStatus, data.error);
      } else {
        window.CV2Render.render(
          previewBox,
          window.CV2Render.snapshotSpec(data.payload, data.message_kind),
          {},
        );
        previewStatus.textContent = "";
      }
    } catch (e) {
      fail(previewStatus, "Render error: " + e);
    } finally {
      previewBtn.disabled = false;
    }
  });

  sendBtn.addEventListener("click", async () => {
    const where = feed.channelId ? "#" + feed.channelId : "its channel";
    const what = publish.checked
      ? "post it to " + where + " and crosspost it to every following server"
      : "post it to " + where + " without crossposting";
    if (!window.confirm("Send the " + feed.title + " post now? This will " + what + ".")) {
      return;
    }
    sendBtn.disabled = true;
    sendStatus.classList.remove("err");
    sendStatus.textContent = "Building…";
    try {
      const res = await window.api("/feed/" + encodeURIComponent(NAME) + "/send", {
        publish: publish.checked,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        sendStatus.textContent = "Send started — check Mirror logs for delivery.";
      } else {
        fail(sendStatus, data.error || "Send failed.");
      }
    } catch (_) {
      fail(sendStatus, "Network error — try again.");
    } finally {
      sendBtn.disabled = false;
    }
  });

  loadFeed().catch((e) => fail(previewStatus, String(e)));
})();
