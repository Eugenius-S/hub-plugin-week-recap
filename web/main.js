// «Неделя»: рекапы прошлых недель — плитки, TL;DR и ссылка на отчёт (HTML дизайнера). Данные — web/data.json
// от процесса плагина (main.py), меняются раз в неделю.
let hub = null, data = null, box = null, open = null;

async function load() {
  try {
    const r = await fetch(hub.url("web/data.json") + "?t=" + Date.now());
    if (r.ok) data = await r.json();
  } catch { /* процесс ещё не написал файл */ }
  paint();
}

const day = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

function week(w) {
  const card = hub.el("section", "wr-week");
  const head = hub.el("button", "wr-head");
  head.append(hub.el("span", "wr-label", `${day(w.start)} — ${day(w.end - 1)}`), hub.el("span", "wr-state", w.stage || ""));
  head.onclick = () => { open = open === w.label ? "" : w.label; paint(); };
  card.append(head);
  if (w.tiles?.length) {
    const tiles = hub.el("div", "wr-tiles");
    for (const t of w.tiles) {
      const tile = hub.el("div", "wr-tile");
      tile.append(hub.el("div", "wr-num", t.value ?? "—"), hub.el("div", "wr-name", t.label || ""));
      tiles.append(tile);
    }
    card.append(tiles);
  }
  if (open === w.label) {
    if (w.text) card.append(hub.el("p", "wr-tldr", w.text));
    if (w.report) {
      const a = hub.el("a", "wr-link", "Открыть отчёт");
      a.href = hub.url(w.report);
      a.target = "_blank";
      a.rel = "noopener";
      card.append(a);
    } else if (!w.text) card.append(hub.el("p", "wr-muted", "Рекапа пока нет."));
  }
  return card;
}

// когда разбирать: пост владельца «расписание: пн 09:00 [пояс]» в канал плагина — процесс берёт последний верный
const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
let editing = false, saved = "";

function when() {
  const w = data?.when || { day: 0, hour: 9, minute: 0, zone: null };
  const row = hub.el("div", "wr-when");
  if (!editing) {
    const edit = hub.el("button", "wr-link", "Изменить");
    edit.onclick = () => { editing = true; saved = ""; paint(); };
    row.append(hub.el("span", "wr-muted", `Рекап: ${data?.schedule || "пн 09:00, местное время"}`), edit);
    if (saved) row.append(hub.el("span", "wr-muted", saved));
    return row;
  }
  const day = hub.el("select");
  DAYS.forEach((d, i) => { const o = hub.el("option", null, d); o.value = d; o.selected = i === w.day; day.append(o); });
  const time = hub.el("input");
  time.type = "time";
  time.value = `${String(w.hour).padStart(2, "0")}:${String(w.minute).padStart(2, "0")}`;
  const first = hub.el("label", "wr-muted");
  const firstBox = hub.el("input");
  firstBox.type = "checkbox";
  firstBox.checked = !!w.start;
  time.disabled = firstBox.checked;
  firstBox.onchange = () => { time.disabled = firstBox.checked; };
  first.append(firstBox, " при первом запуске хаба");
  const zone = hub.el("input", "wr-zone");
  zone.placeholder = "пояс: местное время";
  zone.value = w.zone || "";
  const save = hub.el("button", "wr-link", "Сохранить"), cancel = hub.el("button", "wr-link", "Отмена");
  cancel.onclick = () => { editing = false; paint(); };
  save.onclick = async () => {
    const z = zone.value.trim();
    await hub.postFeed(`расписание: ${day.value} ${firstBox.checked ? "старт" : time.value}${z ? " " + z : ""}`);
    editing = false;
    saved = "сохранено — применится в течение минуты";
    paint();
  };
  row.append(hub.el("span", "wr-muted", "Рекап:"), day, time, first, zone, save, cancel);
  return row;
}

function paint() {
  if (!box) return;
  const weeks = data?.weeks || [];
  if (open === null && weeks[0]) open = weeks[0].label;
  box.replaceChildren(
    hub.el("h2", "wr-title", "Неделя"), when(),
    weeks.length ? hub.el("div", "wr-list") : hub.el("p", "wr-muted",
      "Первый рекап — по расписанию выше: итог прошлой недели и задачи на эту придут сюда и в Telegram."));
  if (weeks.length) box.lastChild.append(...weeks.map(week));
}

export default function register(h) {
  hub = h;
  hub.addStyle("web/style.css");
  hub.addView({
    id: "week-recap", title: "Неделя", subtitle: "рекап недели и задачи", icon: "clock",
    render(root) { if (box !== root) { box = root; box.classList.add("wr"); } paint(); },
    show() { load(); },
  });
}
