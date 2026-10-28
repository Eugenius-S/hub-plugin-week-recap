// "Week": recaps of past weeks - tiles, TL;DR and a link to the report (the designer's HTML). Data - web/data.json
// from the plugin process (main.py), it changes once a week.
let hub = null, data = null, box = null, open = null;

// UI strings: en by default, ru when the hub language is Russian; the language is picked at use time by hub.lang,
// never at module load. The schedule words (keyword, start, days) are also what the tab posts to the plugin channel.
const STR = {
  en: {
    title: "Week", subtitle: "weekly recap and tasks",
    open_report: "Open the report", no_recap: "No recap yet.",
    edit: "Edit", save: "Save", cancel: "Cancel", recap: "Recap:", recap_at: "Recap: {when}",
    local_time: "local time", at_start: "at the first start of the hub", zone_ph: "zone: local time",
    saved: "saved, applies within a minute",
    empty: "The first recap comes by the schedule above: the summary of last week and the tasks for this one will arrive here and in Telegram.",
    ready: "ready", numbers: "numbers only", writing: "the writer is writing the texts", reviewing: "{to} is reviewing",
    keyword: "schedule", start: "start", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    day_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], locale: "en-US",
    blocks_title: "What the recaps show", blocks_hint: "Untick what you do not want to see: it leaves the text, the report and the Telegram message.",
    blocks_week: "Weekly recap", blocks_day: "Daily recap", blocks_saved: "saved, applies to the next recap",
    block: {
      insights: "Insights", numbers: "Numbers", themes: "What was done", tokens: "Tokens", together: "How we worked together",
      todo: "Todo", machines: "By computers", decided: "Decided", wait: "Waiting for you", work: "In progress",
      stuck: "Stuck", backlog: "Backlog", jira: "Jira",
    },
  },
  ru: {
    title: "Неделя", subtitle: "рекап недели и задачи",
    open_report: "Открыть отчёт", no_recap: "Рекапа пока нет.",
    edit: "Изменить", save: "Сохранить", cancel: "Отмена", recap: "Рекап:", recap_at: "Рекап: {when}",
    local_time: "местное время", at_start: "при первом запуске хаба", zone_ph: "пояс: местное время",
    saved: "сохранено, применится в течение минуты",
    empty: "Первый рекап придёт по расписанию выше: итог прошлой недели и задачи на эту появятся здесь и в Telegram.",
    ready: "готов", numbers: "только цифры", writing: "тексты пишет writer", reviewing: "разбирает {to}",
    keyword: "расписание", start: "старт", days: ["пн", "вт", "ср", "чт", "пт", "сб", "вс"],
    day_names: ["пн", "вт", "ср", "чт", "пт", "сб", "вс"], locale: "ru-RU",
    blocks_title: "Что показывают рекапы", blocks_hint: "Сними галочку с того, что не нужно: блок уйдёт из текста, отчёта и сообщения в Telegram.",
    blocks_week: "Недельный рекап", blocks_day: "Дневной рекап", blocks_saved: "сохранено, применится к следующему рекапу",
    block: {
      insights: "Инсайты", numbers: "Цифры", themes: "Что сделано", tokens: "Токены", together: "Как работали вместе",
      todo: "Задачи на неделю", machines: "По компьютерам", decided: "Решено", wait: "Ждёт тебя", work: "В работе",
      stuck: "Застряло", backlog: "Очередь", jira: "Jira",
    },
  },
};
// hub is set in register(); hub.lang is read on every call, never at module load
const lang = () => (STR[hub?.lang] ? hub.lang : "en");
const t = (key, vars) => {
  let r = STR[lang()][key] ?? STR.en[key] ?? key;
  for (const n in vars) r = r.replaceAll(`{${n}}`, vars[n]);
  return r;
};
const list = (key) => STR[lang()][key] ?? STR.en[key];

async function load() {
  try {
    const r = await fetch(hub.url("web/data.json") + "?t=" + Date.now());
    if (r.ok) data = await r.json();
  } catch { /* the process has not written the file yet */ }
  paint();
}

const day = (ts) => new Date(ts * 1000).toLocaleDateString(t("locale"), { day: "numeric", month: "short" });

// the stage is a code from the process ("ready", "numbers", "writing", "reviewing"), worded here in the tab's language
const stage = (w) => (w.stage ? t(w.stage, { to: w.to || "-" }) : "");

function week(w) {
  const card = hub.el("section", "wr-week");
  const head = hub.el("button", "wr-head");
  head.append(hub.el("span", "wr-label", `${day(w.start)} — ${day(w.end - 1)}`), hub.el("span", "wr-state", stage(w)));
  head.onclick = () => { open = open === w.label ? "" : w.label; paint(); };
  card.append(head);
  if (w.tiles?.length) {
    const tiles = hub.el("div", "wr-tiles");
    for (const x of w.tiles) {
      const tile = hub.el("div", "wr-tile");
      tile.append(hub.el("div", "wr-num", x.value ?? "—"), hub.el("div", "wr-name", x.label || ""));
      tiles.append(tile);
    }
    card.append(tiles);
  }
  if (open === w.label) {
    if (w.text) card.append(hub.el("p", "wr-tldr", w.text));
    if (w.report) {
      const a = hub.el("a", "wr-link", t("open_report"));
      a.href = hub.url(w.report);
      a.target = "_blank";
      a.rel = "noopener";
      card.append(a);
    } else if (!w.text) card.append(hub.el("p", "wr-muted", t("no_recap")));
  }
  return card;
}

// when to review: the owner's post "schedule: mon 09:00 [zone]" (the Russian words in a Russian hub) to the plugin
// channel - the process takes the last valid one; it understands both languages whatever the tab speaks
let editing = false, saved = "";

const two = (n) => String(n).padStart(2, "0");

function describe(w) {
  const at = w.start ? t("at_start") : `${two(w.hour)}:${two(w.minute)}`;
  return `${list("day_names")[w.day]} ${at}, ${w.zone || t("local_time")}`;
}

function when() {
  const w = data?.when || { day: 0, hour: 9, minute: 0, zone: null };
  const row = hub.el("div", "wr-when");
  if (!editing) {
    const edit = hub.el("button", "wr-link", t("edit"));
    edit.onclick = () => { editing = true; saved = ""; paint(); };
    row.append(hub.el("span", "wr-muted", t("recap_at", { when: describe(w) })), edit);
    if (saved) row.append(hub.el("span", "wr-muted", saved));
    return row;
  }
  const dayBox = hub.el("select");
  list("days").forEach((d, i) => {
    const o = hub.el("option", null, list("day_names")[i]);
    o.value = d;
    o.selected = i === w.day;
    dayBox.append(o);
  });
  const time = hub.el("input");
  time.type = "time";
  time.value = `${two(w.hour)}:${two(w.minute)}`;
  const first = hub.el("label", "wr-muted");
  const firstBox = hub.el("input");
  firstBox.type = "checkbox";
  firstBox.checked = !!w.start;
  time.disabled = firstBox.checked;
  firstBox.onchange = () => { time.disabled = firstBox.checked; };
  first.append(firstBox, " " + t("at_start"));
  const zone = hub.el("input", "wr-zone");
  zone.placeholder = t("zone_ph");
  zone.value = w.zone || "";
  const save = hub.el("button", "wr-link", t("save")), cancel = hub.el("button", "wr-link", t("cancel"));
  cancel.onclick = () => { editing = false; paint(); };
  save.onclick = async () => {
    const z = zone.value.trim();
    await hub.postFeed(`${t("keyword")}: ${dayBox.value} ${firstBox.checked ? t("start") : time.value}${z ? " " + z : ""}`);
    editing = false;
    saved = t("saved");
    paint();
  };
  row.append(hub.el("span", "wr-muted", t("recap")), dayBox, time, first, zone, save, cancel);
  return row;
}

// which blocks of the recaps are shown: the tab posts "blocks <week|day>: <the switched-off ones, comma separated>" (or "-")
// to the plugin channel - the process writes it into hub.json (a page cannot) within a minute. Until data.json shows the
// same, the choice stays here, so the ticks do not jump back.
const BLOCKS = {
  week: ["insights", "numbers", "themes", "tokens", "together", "todo", "machines"],
  day: ["decided", "wait", "work", "stuck", "backlog", "jira"],
};
const pending = {};
let blocksSaved = "";

const hiddenOf = (kind) => pending[kind] ?? data?.blocks?.[kind]?.hide ?? [];

function blocksBox() {
  const wrap = hub.el("section", "wr-blocks");
  wrap.append(hub.el("h3", "wr-sub", t("blocks_title")), hub.el("p", "wr-muted", t("blocks_hint")));
  for (const kind of ["week", "day"]) {
    const hide = hiddenOf(kind);
    const group = hub.el("div", "wr-group");
    group.append(hub.el("span", "wr-label", t("blocks_" + kind)));
    for (const name of BLOCKS[kind]) {
      const row = hub.el("label", "wr-check");
      const box = hub.el("input");
      box.type = "checkbox";
      box.checked = !hide.includes(name);
      box.onchange = async () => {
        const now = BLOCKS[kind].filter((b) => (b === name ? !box.checked : hiddenOf(kind).includes(b)));
        pending[kind] = now;
        blocksSaved = t("blocks_saved");
        await hub.postFeed(`blocks ${kind}: ${now.length ? now.join(", ") : "-"}`);
        paint();
      };
      row.append(box, " " + (STR[lang()].block[name] ?? STR.en.block[name]));
      group.append(row);
    }
    wrap.append(group);
  }
  if (blocksSaved) wrap.append(hub.el("p", "wr-muted", blocksSaved));
  return wrap;
}

function paint() {
  if (!box) return;
  for (const kind in pending) {
    const now = data?.blocks?.[kind]?.hide;
    if (now && now.join() === pending[kind].join()) delete pending[kind];
  }
  const weeks = data?.weeks || [];
  if (open === null && weeks[0]) open = weeks[0].label;
  box.replaceChildren(
    hub.el("h2", "wr-title", t("title")), when(), blocksBox(),
    weeks.length ? hub.el("div", "wr-list") : hub.el("p", "wr-muted", t("empty")));
  if (weeks.length) box.lastChild.append(...weeks.map(week));
}

export default function register(h) {
  hub = h;
  hub.addStyle("web/style.css");
  hub.addView({
    id: "week-recap", title: t("title"), subtitle: t("subtitle"), icon: "clock",
    render(root) { if (box !== root) { box = root; box.classList.add("wr"); } paint(); },
    show() { load(); },
  });
}
