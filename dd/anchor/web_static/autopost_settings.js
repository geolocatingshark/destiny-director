// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// The autopost settings page's save button.
//
// Extracted from an inline <script> so `script-src 'self'` holds (see SECURITY_HEADERS
// in dd/anchor/web.py). Loaded deferred after shared.js, which defines window.api.

"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("save");
  const status = document.getElementById("status");
  btn.addEventListener("click", async () => {
    const settings = {};
    document
      .querySelectorAll("input[type=checkbox][data-slug]")
      .forEach((el) => {
        settings[el.dataset.slug] = el.checked;
      });
    document
      .querySelectorAll("input.urlfield[data-slug]")
      .forEach((el) => {
        settings[el.dataset.slug] = el.value;
      });
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      const res = await window.api("/autopost_settings/save", { settings });
      if (res.ok) {
        status.textContent = "Saved.";
      } else {
        let msg = "Save failed.";
        try {
          const data = await res.json();
          if (data && data.error) msg = data.error;
        } catch (_) {}
        status.textContent = msg;
      }
    } catch (_) {
      status.textContent = "Network error — try again.";
    } finally {
      btn.disabled = false;
    }
  });
});
