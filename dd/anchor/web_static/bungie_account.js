// Bungie account page — link status plus an on-demand account-numbers lookup.
// Served by dd.anchor.extensions.bungie_account. The shell is static (CSP is
// script-src 'self'), so status comes from GET /bungie/data on load.

(() => {
  const byId = (id) => document.getElementById(id);
  const dot = byId("dot");
  const state = byId("state");
  const expiry = byId("expiry");
  const fetchBtn = byId("fetchBtn");
  const numbersStatus = byId("numbersStatus");
  const numbers = byId("numbers");

  async function loadStatus() {
    try {
      const res = await fetch("/bungie/data");
      if (!res.ok) throw new Error("status unavailable");
      const data = await res.json();

      if (!data.linked) {
        dot.className = "dot bad";
        state.textContent = "Not linked — log in to enable the vendor-backed feeds.";
      } else if (data.expired) {
        dot.className = "dot bad";
        state.textContent = "Link expired — log in again.";
      } else {
        dot.className = "dot ok";
        state.textContent = "Linked.";
      }
      // The stored expiry already carries the 20% safety factor, so it is when the bot
      // gives up on the token, not when Bungie would.
      expiry.textContent = data.expires
        ? (data.expired ? "Expired at " : "Valid until ") + data.expires
        : "";
    } catch (_) {
      dot.className = "dot bad";
      state.textContent = "Could not read link status.";
    }
  }

  fetchBtn.addEventListener("click", async () => {
    fetchBtn.disabled = true;
    numbersStatus.classList.remove("err");
    numbersStatus.textContent = "Fetching…";
    numbers.textContent = "";
    try {
      const res = await fetch("/bungie/account");
      const data = await res.json();
      if (data.error) {
        numbersStatus.classList.add("err");
        numbersStatus.textContent = data.error;
      } else {
        // textContent, not innerHTML — these are ids from a remote API.
        numbers.textContent =
          "Destiny Character ID:   " + data.characterId + "\n" +
          "Destiny Membership ID:  " + data.membershipId + "\n" +
          "Destiny Membership Type:" + " " + data.membershipType;
        numbersStatus.textContent = "";
      }
    } catch (_) {
      numbersStatus.classList.add("err");
      numbersStatus.textContent = "Network error — try again.";
    } finally {
      fetchBtn.disabled = false;
    }
  });

  loadStatus();
})();
