// «Неделя»: разборы прошлых недель — цифры связки владелец + хаб и текст разбора (задачи, Jira, эффективность,
// рекомендации). Данные — web/data.json от процесса плагина (main.py), меняются раз в неделю.
let hub = null, data = null, box = null, open = null;
const KEY = ["owner_messages", "tasks_done", "forms", "task_median_min", "busy_hours"];

async function load() {
  try {
    const r = await fetch(hub.url("web/data.json") + "?t=" + Date.now());
    if (r.ok) data = await r.json();
  } catch { /* процесс ещё не написал файл */ }
  paint();
}

const day = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

// строка разбора: **жирное** и `код` — без HTML из текста
function inline(text) {
  const p = hub.el("span");
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**")) p.append(hub.el("b", null, part.slice(2, -2)));
    else if (part.startsWith("`") && part.endsWith("`")) p.append(hub.el("code", null, part.slice(1, -1)));
    else p.append(part);
  }
  return p;
}

function prose(text) {
  const root = hub.el("div", "wr-text");
  let list = null;
  for (const line of text.split("\n")) {
    const h = line.match(/^(#{1,4})\s+(.*)/), li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (li) {
      if (!list) root.append(list = hub.el("ul"));
      const item = hub.el("li");
      item.append(inline(li[1]));
      list.append(item);
      continue;
    }
    list = null;
    if (h) root.append(hub.el(h[1].length <= 2 ? "h3" : "h4", null, h[2]));
    else if (line.trim()) { const para = hub.el("p"); para.append(inline(line)); root.append(para); }
  }
  return root;
}

function week(w) {
  const card = hub.el("section", "wr-week");
  const head = hub.el("button", "wr-head");
  head.append(hub.el("span", "wr-label", `${day(w.start)} — ${day(w.end - 1)}`),
              hub.el("span", "wr-state", w.text ? "разбор готов" : w.notified ? "только цифры" : `разбирает ${w.to || "—"}`));
  head.onclick = () => { open = open === w.label ? "" : w.label; paint(); };
  const tiles = hub.el("div", "wr-tiles");
  for (const k of KEY) {
    const t = hub.el("div", "wr-tile");
    t.append(hub.el("div", "wr-num", w.numbers[k] ?? "—"), hub.el("div", "wr-name", data.names[k] || k));
    tiles.append(t);
  }
  card.append(head, tiles);
  if (open === w.label) {
    const all = hub.el("dl", "wr-all");
    for (const [k, v] of Object.entries(w.numbers)) all.append(hub.el("dt", null, data.names[k] || k), hub.el("dd", null, v ?? "—"));
    card.append(w.text ? prose(w.text) : hub.el("p", "wr-muted", "Разбора нет — только цифры."), all,
                hub.el("p", "wr-muted", `Файлы: ${w.recap} · ${w.digest}`));
  }
  return card;
}

function paint() {
  if (!box) return;
  const weeks = data?.weeks || [];
  if (open === null && weeks[0]) open = weeks[0].label;
  box.replaceChildren(
    hub.el("h2", "wr-title", "Неделя"),
    weeks.length ? hub.el("div", "wr-list") : hub.el("p", "wr-muted",
      "Первый разбор — в понедельник в 9:00: сводка прошлой недели уйдёт analyst (нет его — хабу), итог придёт сюда и в Telegram."));
  if (weeks.length) box.lastChild.append(...weeks.map(week));
}

export default function register(h) {
  hub = h;
  hub.addStyle("web/style.css");
  hub.addView({
    id: "week-recap", title: "Неделя", subtitle: "разбор прошлой недели", icon: "clock",
    render(root) { if (box !== root) { box = root; box.classList.add("wr"); } paint(); },
    show() { load(); },
  });
}
