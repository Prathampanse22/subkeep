// SubKeep front end — plain JavaScript, no build step.
"use strict";

const CURRENCIES = ["USD", "EUR", "GBP", "INR", "CAD", "AUD", "JPY", "CNY", "CHF", "SEK", "NOK", "DKK",
  "NZD", "SGD", "HKD", "ZAR", "BRL", "MXN", "AED", "SAR", "KRW", "PLN", "TRY", "IDR", "PHP", "THB",
  "MYR", "VND", "NGN", "KES", "EGP", "PKR", "BDT", "ILS", "CZK", "HUF", "RON", "ARS", "CLP", "COP"];
const CYCLE_LABEL = { weekly: "Weekly", monthly: "Monthly", quarterly: "Every 3 months", yearly: "Yearly" };

const state = {
  settings: { currency: "USD", theme: "system" },
  categories: [],
  subscriptions: [],
  expenses: [],
  expenseMonth: monthKey(new Date()), // null = all
  calMonth: monthKey(new Date()),
  calData: null,
  calSelected: null, // ISO date of the selected day
};
const VIEWS = ["dashboard", "subscriptions", "expenses", "calendar", "settings"];

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Create an element. Text is always set with textContent, so user data can't inject HTML. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(node.style, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : String(c));
  return node;
}

function monthKey(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; }
function todayISO() { const d = new Date(); return `${monthKey(d)}-${String(d.getDate()).padStart(2, "0")}`; }

function money(n) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: state.settings.currency }).format(n || 0);
  } catch { return `${state.settings.currency} ${(n || 0).toFixed(2)}`; }
}
function niceDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
function niceMonth(key) {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}
function shiftMonth(key, delta) {
  const [y, m] = key.split("-").map(Number);
  return monthKey(new Date(y, m - 1 + delta, 1));
}
function relDays(n) {
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  return `in ${n} days`;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2800);
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

// ----------------------------------------------------------------- views
function showView(name) {
  $$(".tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === name)));
  $$(".view").forEach((v) => { v.hidden = v.id !== `view-${name}`; });
  location.hash = name;
  refresh(name);
}

async function refresh(view = currentView()) {
  try {
    if (view === "dashboard") await loadDashboard();
    if (view === "subscriptions") { state.subscriptions = await api("/api/subscriptions"); renderSubscriptions(); }
    if (view === "expenses") await loadExpenses();
    if (view === "calendar") await loadCalendar();
    if (view === "settings") renderSettings();
  } catch (e) { toast(e.message, true); }
}
function currentView() { return ($(".tabs [aria-selected=true]") || {}).dataset?.view || "dashboard"; }

// ------------------------------------------------------------- dashboard
async function loadDashboard() {
  const s = await api("/api/summary");
  $("#stat-sub-month").textContent = money(s.subscriptions_monthly);
  $("#stat-sub-count").textContent = `${s.active_subscriptions} active`;
  $("#stat-sub-year").textContent = money(s.subscriptions_yearly);
  $("#stat-exp-month").textContent = money(s.expenses_this_month);
  $("#stat-total").textContent = money(s.total_this_month);

  const up = $("#upcoming");
  up.replaceChildren();
  if (!s.upcoming_renewals.length) up.append(el("li", { class: "muted", text: "Nothing renews in the next 30 days." }));
  for (const sub of s.upcoming_renewals) {
    up.append(el("li", { class: "list-row clickable", onclick: () => openSubscription(sub) },
      el("span", { class: "grow" }, el("strong", { text: sub.name }), " ",
        el("span", { class: "muted", text: `${niceDate(sub.next_renewal)} · ${relDays(sub.days_until_renewal)}` })),
      el("span", { class: "num", text: money(sub.cost) })));
  }

  const bars = $("#by-category");
  bars.replaceChildren();
  const max = Math.max(1, ...s.by_category.map((c) => c.amount));
  if (!s.by_category.length) bars.append(el("p", { class: "muted", text: "Add subscriptions or expenses to see a breakdown." }));
  for (const c of s.by_category) {
    bars.append(el("div", { class: "bar-row" },
      el("span", { class: "bar-label", text: c.category }),
      el("span", { class: "bar-track" }, el("span", { class: "bar-fill", style: { width: `${(c.amount / max) * 100}%` } })),
      el("span", { class: "num", text: money(c.amount) })));
  }

  const trend = $("#trend");
  trend.replaceChildren();
  const tmax = Math.max(1, ...s.trend.map((t) => t.subscriptions + t.expenses));
  for (const t of s.trend) {
    const total = t.subscriptions + t.expenses;
    trend.append(el("div", { class: "trend-col", title: `${niceMonth(t.month)}: ${money(total)}` },
      el("span", { class: "trend-val", text: money(total) }),
      el("div", { class: "trend-stack" },
        el("span", { class: "seg exp", style: { height: `${(t.expenses / tmax) * 100}%` } }),
        el("span", { class: "seg sub", style: { height: `${(t.subscriptions / tmax) * 100}%` } })),
      el("span", { class: "trend-label", text: niceMonth(t.month).split(" ")[0].slice(0, 3) })));
  }
}

// --------------------------------------------------------- subscriptions
function renderSubscriptions() {
  const q = $("#sub-search").value.trim().toLowerCase();
  const status = $("#sub-filter").value;
  const rows = state.subscriptions.filter((s) =>
    (!status || s.status === status) &&
    (!q || `${s.name} ${s.category || ""} ${s.notes}`.toLowerCase().includes(q)));
  const body = $("#sub-rows");
  body.replaceChildren(...rows.map((s) => el("tr", { class: "clickable", onclick: () => openSubscription(s) },
    el("td", {}, el("strong", { text: s.name })),
    el("td", { text: s.category || "—" }),
    el("td", { class: "num", text: money(s.cost) }),
    el("td", { text: CYCLE_LABEL[s.cycle] }),
    el("td", { class: "num", text: money(s.monthly_cost) }),
    el("td", { text: s.next_renewal ? `${niceDate(s.next_renewal)}` : "—" }),
    el("td", {}, el("span", { class: `badge ${s.status}`, text: s.status })),
    el("td", { class: "num" }, el("button", { class: "btn small ghost", text: "Edit", "aria-label": `Edit ${s.name}` })))));
  $("#sub-empty").hidden = rows.length > 0;
  if (!state.subscriptions.length) $("#sub-empty").textContent = "No subscriptions yet. Add your first one — Netflix, Spotify, your phone plan…";
  else if (!rows.length) $("#sub-empty").textContent = "No subscriptions match your search.";
}

function fillCategorySelects() {
  for (const sel of $$(".cat-select")) {
    const keep = sel.value;
    sel.replaceChildren(el("option", { value: "", text: "— None —" }),
      ...state.categories.map((c) => el("option", { value: c.id, text: c.name })));
    sel.value = keep;
  }
}

function openDialog(dialog, item, defaults) {
  const form = $("form", dialog);
  form.reset();
  $(".form-error", form).textContent = "";
  fillCategorySelects();
  const values = item || defaults;
  for (const [k, v] of Object.entries(values)) {
    if (form.elements[k]) form.elements[k].value = v ?? "";
  }
  form.dataset.id = item ? item.id : "";
  $("[data-delete]", form).hidden = !item;
  dialog.showModal();
  form.elements[0].focus();
}

function openSubscription(sub) {
  $("#sub-dialog-title").textContent = sub ? "Edit subscription" : "Add subscription";
  openDialog($("#sub-dialog"), sub, { cycle: "monthly", status: "active", next_renewal: "" });
}
function openExpense(exp, onDate) {
  $("#exp-dialog-title").textContent = exp ? "Edit expense" : "Add expense";
  const defMonth = state.expenseMonth && state.expenseMonth !== monthKey(new Date()) ? `${state.expenseMonth}-01` : todayISO();
  openDialog($("#exp-dialog"), exp, { spent_on: onDate || defMonth });
}

function wireDialog(dialog, resource, label) {
  const form = $("form", dialog);
  $("[data-close]", form).addEventListener("click", () => dialog.close());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $(".form-error", form);
    const invalid = [...form.elements].find((f) => f.willValidate && !f.checkValidity());
    if (invalid) { err.textContent = `Please check “${invalid.closest("label").firstChild.textContent.replace("*", "").trim()}”.`; invalid.focus(); return; }
    const data = Object.fromEntries(new FormData(form));
    const id = form.dataset.id;
    try {
      await api(id ? `/api/${resource}/${id}` : `/api/${resource}`, { method: id ? "PUT" : "POST", body: data });
      dialog.close();
      toast(id ? `${label} updated` : `${label} added`);
      refresh();
    } catch (ex) { err.textContent = ex.message; }
  });
  $("[data-delete]", form).addEventListener("click", async () => {
    const id = form.dataset.id;
    if (!id || !confirm(`Delete this ${label.toLowerCase()}? This can't be undone.`)) return;
    try {
      await api(`/api/${resource}/${id}`, { method: "DELETE" });
      dialog.close();
      toast(`${label} deleted`);
      refresh();
    } catch (ex) { $(".form-error", form).textContent = ex.message; }
  });
}

// --------------------------------------------------------------- expenses
async function loadExpenses() {
  const m = state.expenseMonth;
  state.expenses = await api(m ? `/api/expenses?month=${m}` : "/api/expenses");
  $("#month-label").textContent = m ? niceMonth(m) : "All expenses";
  $("#month-all").textContent = m ? "Show all" : "This month";
  $("#month-prev").disabled = $("#month-next").disabled = !m;
  const total = state.expenses.reduce((a, e) => a + e.amount, 0);
  $("#exp-total").textContent = money(total);
  $("#exp-rows").replaceChildren(...state.expenses.map((e) => el("tr", { class: "clickable", onclick: () => openExpense(e) },
    el("td", { text: niceDate(e.spent_on) }),
    el("td", {}, el("strong", { text: e.description })),
    el("td", { text: e.category || "—" }),
    el("td", { text: e.payment_method || "—" }),
    el("td", { class: "num", text: money(e.amount) }),
    el("td", { class: "num" }, el("button", { class: "btn small ghost", text: "Edit", "aria-label": `Edit ${e.description}` })))));
  $("#exp-empty").hidden = state.expenses.length > 0;
}

// --------------------------------------------------------------- calendar
const WEEK_START = 1; // Monday

async function loadCalendar() {
  state.calData = await api(`/api/calendar?month=${state.calMonth}`);
  renderCalendar();
}

function renderCalendar() {
  const data = state.calData;
  const [y, m] = state.calMonth.split("-").map(Number);
  $("#cal-label").textContent = niceMonth(state.calMonth);
  $("#cal-renewals").textContent = money(data.renewals_total);
  $("#cal-expenses").textContent = money(data.expenses_total);

  // Weekday names in the user's language, starting Monday (Jan 1 2024 was a Monday).
  const fmt = new Intl.DateTimeFormat(undefined, { weekday: "short" });
  $("#cal-head").replaceChildren(...Array.from({ length: 7 }, (_, i) =>
    el("span", { text: fmt.format(new Date(2024, 0, 1 + ((i + WEEK_START - 1 + 7) % 7))) })));

  const daysInMonth = new Date(y, m, 0).getDate();
  const lead = (new Date(y, m - 1, 1).getDay() - WEEK_START + 7) % 7;
  const today = todayISO();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(el("div", { class: "cal-cell blank", "aria-hidden": "true" }));
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${state.calMonth}-${String(d).padStart(2, "0")}`;
    const info = data.days[iso] || { renewals: [], expenses: [] };
    const items = [
      ...info.renewals.map((r) => el("span", { class: "cal-chip sub", text: `${r.name} · ${money(r.cost)}` })),
      ...info.expenses.map((e) => el("span", { class: "cal-chip exp", text: `${e.description} · ${money(e.amount)}` })),
    ];
    const shown = items.slice(0, 3);
    if (items.length > 3) shown.push(el("span", { class: "cal-more", text: `+${items.length - 3} more` }));
    const count = items.length;
    cells.push(el("button", {
      type: "button",
      class: `cal-cell${iso === today ? " today" : ""}${iso === state.calSelected ? " selected" : ""}${count ? " has-items" : ""}`,
      "aria-label": `${niceDate(iso)}${count ? `, ${count} item${count > 1 ? "s" : ""}` : ""}`,
      "aria-pressed": String(iso === state.calSelected),
      onclick: () => selectDay(iso),
    }, el("span", { class: "cal-num", text: d }), el("span", { class: "cal-items" }, shown)));
  }
  const trail = (7 - (cells.length % 7)) % 7;
  for (let i = 0; i < trail; i++) cells.push(el("div", { class: "cal-cell blank", "aria-hidden": "true" }));
  $("#cal-grid").replaceChildren(...cells);
  renderDayDetail();
}

function selectDay(iso) {
  state.calSelected = iso;
  renderCalendar();
  if (window.innerWidth < 760) $("#cal-detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDayDetail() {
  const iso = state.calSelected;
  const list = $("#cal-detail-list");
  const addBtn = $("#cal-add-expense");
  if (!iso || !iso.startsWith(state.calMonth)) {
    $("#cal-detail-title").textContent = "Pick a day";
    addBtn.hidden = true;
    list.replaceChildren(el("li", { class: "muted", text: "Click any day to see what renews or what you spent." }));
    return;
  }
  const info = state.calData.days[iso] || { renewals: [], expenses: [] };
  $("#cal-detail-title").textContent = niceDate(iso);
  addBtn.hidden = false;
  const rows = [
    ...info.renewals.map((r) => el("li", { class: "list-row clickable", onclick: () => editSubscriptionById(r.id) },
      el("span", { class: "dot sub" }),
      el("span", { class: "grow" }, el("strong", { text: r.name }), " ",
        el("span", { class: "muted", text: `renews · ${CYCLE_LABEL[r.cycle].toLowerCase()}${r.category ? ` · ${r.category}` : ""}` })),
      el("span", { class: "num", text: money(r.cost) }))),
    ...info.expenses.map((e) => el("li", { class: "list-row clickable", onclick: () => editExpenseById(e.id, iso) },
      el("span", { class: "dot exp" }),
      el("span", { class: "grow" }, el("strong", { text: e.description }), " ",
        el("span", { class: "muted", text: `expense${e.category ? ` · ${e.category}` : ""}` })),
      el("span", { class: "num", text: money(e.amount) }))),
  ];
  list.replaceChildren(...(rows.length ? rows : [el("li", { class: "muted", text: "Nothing on this day." })]));
}

async function editSubscriptionById(id) {
  try {
    state.subscriptions = await api("/api/subscriptions");
    const sub = state.subscriptions.find((s) => s.id === id);
    if (sub) openSubscription(sub);
  } catch (e) { toast(e.message, true); }
}

async function editExpenseById(id, iso) {
  try {
    const list = await api(`/api/expenses?month=${iso.slice(0, 7)}`);
    const exp = list.find((x) => x.id === id);
    if (exp) openExpense(exp);
  } catch (e) { toast(e.message, true); }
}

// --------------------------------------------------------------- settings
function renderSettings() {
  const cur = $("#set-currency");
  const list = CURRENCIES.includes(state.settings.currency) ? CURRENCIES : [state.settings.currency, ...CURRENCIES];
  cur.replaceChildren(...list.map((c) => el("option", { value: c, text: c })));
  cur.value = state.settings.currency;
  $$("#theme-picker [role=radio]").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.themeValue === (state.settings.theme || "system"))));
  $("#cat-list").replaceChildren(...state.categories.map((c) => el("li", { class: "chip" },
    el("span", { text: c.name }),
    el("button", { class: "chip-x", "aria-label": `Delete category ${c.name}`, text: "×", onclick: () => deleteCategory(c) }))));
}

async function deleteCategory(c) {
  if (!confirm(`Delete the category “${c.name}”? Items in it become uncategorized.`)) return;
  try {
    await api(`/api/categories/${c.id}`, { method: "DELETE" });
    state.categories = await api("/api/categories");
    renderSettings();
  } catch (e) { toast(e.message, true); }
}

async function saveSetting(key, value) {
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: { [key]: value } });
    applyTheme();
    toast("Saved");
  } catch (e) { toast(e.message, true); }
}

function applyTheme() {
  document.documentElement.dataset.theme = state.settings.theme || "system";
}

// ------------------------------------------------------------------ boot
async function boot() {
  $$(".tabs [role=tab]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $$("[data-action=new-subscription]").forEach((b) => b.addEventListener("click", () => openSubscription(null)));
  $$("[data-action=new-expense]").forEach((b) => b.addEventListener("click", () => openExpense(null)));
  wireDialog($("#sub-dialog"), "subscriptions", "Subscription");
  wireDialog($("#exp-dialog"), "expenses", "Expense");

  $("#sub-search").addEventListener("input", renderSubscriptions);
  $("#sub-filter").addEventListener("change", renderSubscriptions);
  $("#month-prev").addEventListener("click", () => { state.expenseMonth = shiftMonth(state.expenseMonth, -1); loadExpenses(); });
  $("#month-next").addEventListener("click", () => { state.expenseMonth = shiftMonth(state.expenseMonth, 1); loadExpenses(); });
  $("#month-all").addEventListener("click", () => { state.expenseMonth = state.expenseMonth ? null : monthKey(new Date()); loadExpenses(); });

  $("#set-currency").addEventListener("change", (e) => saveSetting("currency", e.target.value));
  $$("#theme-picker [role=radio]").forEach((b) => b.addEventListener("click", async () => {
    await saveSetting("theme", b.dataset.themeValue);
    renderSettings();
  }));
  $("#cal-prev").addEventListener("click", () => { state.calMonth = shiftMonth(state.calMonth, -1); loadCalendar(); });
  $("#cal-next").addEventListener("click", () => { state.calMonth = shiftMonth(state.calMonth, 1); loadCalendar(); });
  $("#cal-today").addEventListener("click", () => { state.calMonth = monthKey(new Date()); state.calSelected = todayISO(); loadCalendar(); });
  $("#cal-add-expense").addEventListener("click", () => openExpense(null, state.calSelected));
  $("#cat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/categories", { method: "POST", body: { name: $("#cat-name").value } });
      $("#cat-name").value = "";
      state.categories = await api("/api/categories");
      renderSettings();
    } catch (ex) { toast(ex.message, true); }
  });
  $("#import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (!confirm("Restoring replaces ALL current data with the backup. Continue?")) { e.target.value = ""; return; }
    try {
      const data = JSON.parse(await file.text());
      const r = await api("/api/import/json", { method: "POST", body: data });
      await loadBase();
      renderSettings();
      toast(`Restored ${r.subscriptions} subscriptions and ${r.expenses} expenses`);
    } catch (ex) { toast(ex.message.includes("JSON") ? "That file isn't a valid backup" : ex.message, true); }
    e.target.value = "";
  });

  await loadBase();
  const health = await api("/api/health").catch(() => ({}));
  $("#about").textContent = `SubKeep ${health.version || ""} — a private subscription & expense tracker. Everything is stored in a file on this computer; nothing is sent anywhere.`;
  const start = location.hash.slice(1);
  showView(VIEWS.includes(start) ? start : "dashboard");
}

async function loadBase() {
  [state.settings, state.categories] = await Promise.all([api("/api/settings"), api("/api/categories")]);
  applyTheme();
}

boot().catch((e) => toast(`Couldn't reach SubKeep: ${e.message}`, true));
