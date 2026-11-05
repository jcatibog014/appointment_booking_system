// Extras only: every page works without this file.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.hidden = false;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("shiromoto-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("shiromoto-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

function setUpMenu() {
  const button = document.querySelector("[data-menu-button]");
  const menu = document.getElementById(button && button.getAttribute("aria-controls"));
  if (!button || !menu) return;
  button.hidden = false;
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open);
    menu.classList.toggle("is-open", open);
  };
  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

function setUpUserMenu() {
  const menu = document.querySelector("[data-user-menu]");
  if (!menu) return;
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
}

function setUpConfirm() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
}

// Messages float as toasts. Good news fades after 5 seconds (not while pointed at or
// focused); a problem stays until closed, so nobody misses it.
function setUpToasts() {
  document.querySelectorAll("[data-flash]").forEach((toast) => {
    const close = toast.querySelector("[data-flash-close]");
    close.hidden = false;
    const leave = () => {
      toast.classList.add("is-leaving");
      setTimeout(() => toast.remove(), 300);
    };
    close.addEventListener("click", leave);
    if ("stays" in toast.dataset) return;
    let timer = setTimeout(leave, 5000);
    const pause = () => clearTimeout(timer);
    const resume = () => { clearTimeout(timer); timer = setTimeout(leave, 3000); };
    toast.addEventListener("mouseenter", pause);
    toast.addEventListener("focusin", pause);
    toast.addEventListener("mouseleave", resume);
    toast.addEventListener("focusout", resume);
  });
}

// "Copy" buttons next to an invite link or a calendar link. Where the browser has no
// clipboard (an http address), the text is selected for Ctrl+C instead.
function setUpCopy() {
  document.querySelectorAll("[data-copy-button]").forEach((button) => {
    const source = button.closest(".card, .setting, .new-link").querySelector("[data-copy-text]");
    if (!source) return;
    if (!navigator.clipboard && !("copyAnywhere" in button.dataset)) return;
    button.hidden = false;
    const label = button.textContent;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(source.value || source.textContent.trim());
        button.textContent = "Copied";
      } catch (error) {
        if (source.select) source.select();
        button.textContent = "Selected: press Ctrl+C";
      }
      setTimeout(() => { button.textContent = label; }, 2500);
    });
  });
}

// Settings: each Save button looks asleep until its form has a change, then wakes up
// and says so. It still works either way (and without JavaScript it is always awake).
function setUpUnsavedChanges() {
  document.querySelectorAll("form[data-watch-changes]").forEach((form) => {
    const button = form.querySelector("button[type=submit]");
    if (!button || button.closest("fieldset:disabled")) return;
    const note = document.createElement("span");
    note.className = "unsaved-note";
    note.textContent = "Unsaved changes";
    note.hidden = true;
    button.before(note);
    const snapshot = () => new URLSearchParams(new FormData(form)).toString();
    const start = snapshot();
    const update = () => {
      const changed = snapshot() !== start;
      button.classList.toggle("is-waiting", !changed);
      note.hidden = !changed;
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });
}

// A click anywhere on a table row opens its page (the title is the real link, for keyboards).
function setUpRowLinks() {
  document.querySelectorAll("tr[data-href]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select, label, details")) return;
      if (String(getSelection())) return;  // selecting text to copy isn't a click to open
      location.href = row.dataset.href;
    });
  });
}

// A select that applies itself (View As in the demo): the button is only for no-JavaScript.
function setUpAutoSubmit() {
  document.querySelectorAll("select[data-auto-submit]").forEach((select) => {
    const form = select.form;
    const button = form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    select.addEventListener("change", () => form.requestSubmit());
  });
}

// The held time's countdown. The server decides what really happens when it runs
// out (another person may take the time); this only shows how long is left.
function setUpCountdown() {
  const timer = document.querySelector("[data-countdown]");
  if (!timer) return;
  const text = timer.querySelector("[data-countdown-text]");
  const done = document.querySelector("[data-countdown-done]");
  const ends = Date.now() + Number(timer.dataset.countdown) * 1000;
  const tick = () => {
    const left = Math.max(0, Math.round((ends - Date.now()) / 1000));
    text.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    timer.classList.toggle("is-low", left <= 60);
    if (left === 0) {
      timer.hidden = true;
      if (done) done.hidden = false;
      return;
    }
    setTimeout(tick, 1000);
  };
  tick();
}

// ---------- The phone app ----------

// The service worker keeps the look and an offline page, and shows push notices.
function setUpServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").catch(() => {
    // Without it the site still works; it just isn't installable.
  });
}

// "Install the app" in Settings: Chrome and Edge offer it; iPhones use Share > Add to Home Screen.
function setUpInstall() {
  const card = document.querySelector("[data-install-card]");
  if (!card) return;
  const button = card.querySelector("[data-install-button]");
  const done = card.querySelector("[data-install-done]");
  if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
    done.hidden = false;
    return;
  }
  let offer = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    offer = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!offer) return;
    offer.prompt();
    const choice = await offer.userChoice;
    if (choice.outcome === "accepted") {
      button.hidden = true;
      done.hidden = false;
    }
    offer = null;
  });
}

// The server's public push key comes as base64url; the browser wants raw bytes.
function keyBytes(text) {
  const padded = (text + "===".slice((text.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

async function sendSubscription(url, body) {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("The server didn't save it.");
}

// "Turn on notifications": on a booking's page (for that booking) or in Settings (for the account).
function setUpPush() {
  const card = document.querySelector("[data-push-card]");
  const key = document.querySelector('meta[name="push-key"]');
  if (!card || !key || !("serviceWorker" in navigator) || !("PushManager" in window)) return;
  const on = card.querySelector("[data-push-on]");
  const off = card.querySelector("[data-push-off]");
  const state = card.querySelector("[data-push-state]");
  const ref = card.dataset.pushRef;
  card.hidden = false;
  const show = (subscribed, message) => {
    on.hidden = subscribed;
    if (off) off.hidden = !subscribed;
    state.textContent = message || (subscribed ? "Notifications are on for this device." : "");
  };
  navigator.serviceWorker.ready.then((registration) => registration.pushManager.getSubscription())
    .then((existing) => show(Boolean(existing)));
  on.addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        show(false, "Notifications are blocked. Allow them in the browser's site settings.");
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: keyBytes(key.content),
      });
      await sendSubscription("/push/subscribe", { ...subscription.toJSON(), ref });
      show(true);
    } catch (error) {
      show(false, "This browser couldn't turn on notifications. On an iPhone, install the app first.");
    }
  });
  if (off) {
    off.addEventListener("click", async () => {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await sendSubscription("/push/unsubscribe", { endpoint: subscription.endpoint }).catch(() => {});
        await subscription.unsubscribe();
      }
      show(false, "Notifications are off for this device.");
    });
  }
}

setUpTheme();
setUpServiceWorker();
setUpInstall();
setUpPush();
setUpCountdown();
setUpToasts();
setUpMenu();
setUpUserMenu();
setUpConfirm();
setUpCopy();
setUpUnsavedChanges();
setUpRowLinks();
setUpAutoSubmit();
