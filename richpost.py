"""The weekly recap as a Telegram rich message (hub `telegram.rich`): a markdown with two pictures, built from recap.json.

build(data, lang, folder) writes <folder>/recap.md and, if Pillow and a font with the needed letters are there,
numbers.png (the week's numbers in tiles) and chart-tokens.png (the share of tokens by theme). No Pillow or no font -
no pictures: the numbers become a list and the shares a table. The rich limits of the core (32768 characters, 50 files)
are kept: a long recap is shortened step by step. Nothing here talks to the hub: it only reads the data and writes files.
"""
import html
import os
import re

MD = "recap.md"
NUMBERS = "numbers.png"
CHART = "chart-tokens.png"
LIMIT = 30000            # the core takes 32768 characters; the margin is for the title and the quotes
WIDTH = 1080             # the picture width: Telegram scales it down to a phone screen
STATES = {"done": "\u2705", "progress": "\U0001f6e0", "dropped": "\u23f8"}
NBSP = "\u00a0"

STR = {
    "en": {
        "legend": "\u2705 done \u00b7 \U0001f6e0 in progress \u00b7 \u23f8 stopped",
        "tokens_h": "Where the tokens went",
        "tokens_table": "Tokens by theme in numbers",
        "col_theme": "Theme", "col_tokens": "Tokens", "col_share": "Share", "col_done": "Done",
        "x_of_y": "{d} of {n}", "done_of": "done {d} of {n}", "under_one": "under 1%",
        "pain_h": "What hurt and what to try", "try": "Try:",
        "themes_h": "What was done by theme",
        "first": "First try.", "next": "Next:",
        "together_h": "How we worked together", "good": "Went well", "slow": "Slowed us down",
        "todo_h": "For this week", "filed": "Already in Jira", "not_filed": "Not filed anywhere",
        "numbers_h": "The week in numbers", "chart_h": "Where the tokens went",
        "numbers_alt": "The week in numbers", "chart_alt": "Share of tokens by theme",
    },
    "ru": {
        "legend": "\u2705 сдано \u00b7 \U0001f6e0 в работе \u00b7 \u23f8 остановлено",
        "tokens_h": "Куда ушли токены",
        "tokens_table": "Токены по темам цифрами",
        "col_theme": "Тема", "col_tokens": "Токены", "col_share": "Доля", "col_done": "Сдано",
        "x_of_y": "{d} из {n}", "done_of": "сдано {d} из {n}", "under_one": "меньше 1%",
        "pain_h": "Что было больно и что попробовать", "try": "Попробовать:",
        "themes_h": "Что сделано по темам",
        "first": "С первого раза.", "next": "Дальше:",
        "together_h": "Как работали вместе", "good": "Получилось", "slow": "Тормозило",
        "todo_h": "На эту неделю", "filed": "Уже в Jira", "not_filed": "Не заведено нигде",
        "numbers_h": "Неделя в числах", "chart_h": "Куда ушли токены",
        "numbers_alt": "Неделя в числах", "chart_alt": "Доля токенов по темам",
    },
}


def t(lang, key, **kw):
    table = STR.get(lang) or STR["en"]
    return (table.get(key) or STR["en"][key]).format(**kw)


def text(x):
    """One line, single spaces; a non-breaking space (the thousands in numbers) stays."""
    return re.sub(r"[^\S\u00a0]+", " ", str(x or "")).strip()


def esc(x):
    """Text for markdown and the HTML tags around it: nothing in the model's words may open a tag or break a table."""
    return html.escape(text(x), quote=False).replace("|", "\\|")


def sentence(x):
    x = text(x)
    return x if not x or x[-1] in ".!?\u2026:" else x + "."


def tokens(x):
    """'1452M' -> '1 452M' (a thousands space that does not wrap), the model's own spelling otherwise."""
    m = re.match(r"^(\d{4,})([.,]\d+)?\s*([^\d\s]*)$", text(x))
    if not m:
        return text(x)
    whole = f"{int(m[1]):,}".replace(",", NBSP)
    return f"{whole}{m[2] or ''}{m[3]}"


def percent(share):
    try:
        v = float(share)
    except (TypeError, ValueError):
        return None
    return v * 100 if v <= 1 else v


def counted(theme):
    items = [i for i in theme.get("items") or [] if isinstance(i, dict)]
    return sum(1 for i in items if i.get("state") == "done"), len(items)


def by_share(data):
    """The themes that have a share, the biggest first (a stable order for the model's own ties)."""
    return sorted((x for x in data.get("themes") or [] if isinstance(x, dict) and percent(x.get("share")) is not None),
                  key=lambda x: -percent(x["share"]))


def shown(lang, share):
    p = percent(share)
    if p is None:
        return ""
    return t(lang, "under_one") if p < 1 else f"{round(p)}%"


# ---- pictures -------------------------------------------------------------------------------------------------------

FONTS = (   # regular, bold: the first pair that opens and draws both Latin and Cyrillic
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)
INK, MUTED, PAPER, CARD, LINE = (26, 26, 26), (112, 112, 112), (247, 247, 247), (255, 255, 255), (234, 234, 234)


def fonts():
    """(open(size, bold)) for the first font pair found, None - no Pillow or no font."""
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for regular, bold in FONTS:
        if os.path.isfile(regular) and os.path.isfile(bold):
            try:
                ImageFont.truetype(regular, 20)
                ImageFont.truetype(bold, 20)
            except OSError:
                continue
            return lambda size, heavy=False: ImageFont.truetype(bold if heavy else regular, size)
    return None


def fit(draw, s, font_for, size, heavy, room):
    """The text in the largest font up to `size` that fits `room`; cut with an ellipsis at the smallest."""
    while size > 22 and draw.textlength(s, font=font_for(size, heavy)) > room:
        size -= 2
    font = font_for(size, heavy)
    while len(s) > 1 and draw.textlength(s, font=font) > room:
        s = s[:-2].rstrip() + "\u2026"
    return s, font


def wrap(draw, s, font, room, lines):
    """The text in at most `lines` lines that fit `room`; what does not fit ends the last line with an ellipsis."""
    out, line = [], ""
    for word in s.split():
        if not line or draw.textlength(f"{line} {word}", font=font) <= room:
            line = f"{line} {word}".strip()
        else:
            out.append(line)
            line = word
    out.append(line)
    if len(out) > lines:
        out = out[:lines]
        while len(out[-1]) > 1 and draw.textlength(out[-1] + "\u2026", font=font) > room:
            out[-1] = out[-1][:-1].rstrip()
        out[-1] += "\u2026"
    return [x for x in out if x]


def card(draw, box):
    draw.rounded_rectangle(box, radius=34, fill=CARD, outline=LINE, width=2)


def header(draw, font_for, kicker, title):
    draw.text((50, 52), kicker, font=font_for(28), fill=MUTED)
    draw.text((50, 96), title, font=font_for(44, True), fill=INK)


def draw_numbers(path, lang, data, font_for):
    from PIL import Image, ImageDraw
    nums = [n for n in data.get("numbers") or [] if isinstance(n, dict)][:6]
    rows = (len(nums) + 1) // 2
    top, tile_h, gap = 192, 376, 26
    img = Image.new("RGB", (WIDTH, top + rows * tile_h + (rows - 1) * gap + 50), PAPER)
    d = ImageDraw.Draw(img)
    header(d, font_for, text(data.get("week")), t(lang, "numbers_h"))
    tile_w = (WIDTH - 100 - gap) // 2
    for k, n in enumerate(nums):
        x, y = 50 + (k % 2) * (tile_w + gap), top + (k // 2) * (tile_h + gap)
        card(d, (x, y, x + tile_w, y + tile_h))
        big, _, rest = text(n.get("value")).partition(" ")
        big_font = font_for(124, True)
        d.text((x + 42, y + 36), big, font=big_font, fill=INK)
        if rest:
            after = x + 42 + d.textlength(big, font=big_font) + 18
            s, f = fit(d, rest, font_for, 44, True, x + tile_w - 30 - after)
            d.text((after, y + 108), s, font=f, fill=MUTED)
        s, f = fit(d, text(n.get("label")), font_for, 40, True, tile_w - 84)
        d.text((x + 42, y + 220), s, font=f, fill=INK)
        for j, line in enumerate(wrap(d, text(n.get("hint")), font_for(30), tile_w - 84, 2)):
            d.text((x + 42, y + 276 + j * 40), line, font=font_for(30), fill=MUTED)
    img.save(path)


def draw_chart(path, lang, data, font_for):
    from PIL import Image, ImageDraw
    themes = by_share(data)[:12]
    row_h, top = 156, 192
    img = Image.new("RGB", (WIDTH, top + len(themes) * row_h + 70), PAPER)
    d = ImageDraw.Draw(img)
    header(d, font_for, text(data.get("week")), t(lang, "chart_h"))
    card(d, (50, top, WIDTH - 50, top + len(themes) * row_h + 40))
    biggest = max(percent(x["share"]) for x in themes) or 1
    left, right = 90, WIDTH - 90
    for k, th in enumerate(themes):
        y = top + 36 + k * row_h
        if k:
            d.line((left, y - 22, right, y - 22), fill=LINE, width=2)
        done, n = counted(th)
        tail = t(lang, "done_of", d=done, n=n) if n else ""
        tail_w = d.textlength(tail, font=font_for(30)) if tail else 0
        s, f = fit(d, text(th.get("title")), font_for, 38, False, right - left - tail_w - 30)
        d.text((left, y), s, font=f, fill=INK)
        if tail:
            d.text((right - tail_w, y + 4), tail, font=font_for(30), fill=MUTED)
        p = percent(th["share"])
        label = shown(lang, th["share"])
        bar_max = right - left - 150
        bar = max(14, round(bar_max * p / biggest))
        d.rounded_rectangle((left, y + 60, left + bar, y + 88), radius=14, fill=INK)
        d.text((left + bar + 22, y + 56), label, font=font_for(36, True), fill=INK)
    img.save(path)


def pictures(folder, lang, data):
    """{'numbers': path, 'chart': path} of the pictures that were drawn; {} when they cannot be (no Pillow or font)."""
    font_for = fonts()
    out = {}
    if not font_for:
        return out
    for key, name, draw, has in (
            ("numbers", NUMBERS, draw_numbers, [n for n in data.get("numbers") or [] if isinstance(n, dict)]),
            ("chart", CHART, draw_chart, by_share(data))):
        if not has:
            continue
        path = os.path.join(folder, name)
        try:
            draw(path, lang, data, font_for)
        except Exception:   # noqa: BLE001 - a picture is a nicety: the recap goes without it
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        out[key] = path
    return out


# ---- markdown -------------------------------------------------------------------------------------------------------

def image(path, caption):
    return f'![]({path} "{text(caption).replace(chr(34), "")}")'


def details(summary, lines):
    return [f"<details><summary>{esc(summary)}</summary>", "", *lines, "", "</details>"]


def build_md(data, lang, imgs, title, level=0):
    """The recap's markdown. Steps of fitting the limit: 1 - the long texts cut, 2 - the items without stories, 3 - the
    themes without items (only the heads)."""
    brief = level >= 1
    cut = (lambda s: text(s)[:170].rsplit(" ", 1)[0] + "\u2026" if len(text(s)) > 180 else text(s)) if brief else text
    out = [f"# {esc(title)}", "", esc(data.get("tldr")), ""]
    nums = [n for n in data.get("numbers") or [] if isinstance(n, dict)]
    if imgs.get("numbers"):
        out += [image(imgs["numbers"], t(lang, "numbers_alt")), ""]
    elif nums:
        out += [f"- **{esc(n.get('value'))}** {esc(n.get('label'))}" + (f": {esc(n.get('hint'))}" if text(n.get("hint")) else "")
                for n in nums] + [""]
    themes = [x for x in data.get("themes") or [] if isinstance(x, dict)]
    shares = by_share(data)
    if shares:
        out += [f"## {t(lang, 'tokens_h')}", ""]
        rows = ["| " + " | ".join(t(lang, k) for k in ("col_theme", "col_tokens", "col_share", "col_done")) + " |",
                "|---|---:|---:|---:|"]
        for th in shares:
            done, n = counted(th)
            rows.append(f"| {esc(th.get('title'))} | {esc(tokens(th.get('tokens')))} | {shown(lang, th['share'])} | "
                        f"{t(lang, 'x_of_y', d=done, n=n) if n else ''} |")
        if imgs.get("chart"):
            out += [image(imgs["chart"], t(lang, "chart_alt")), "", *details(t(lang, "tokens_table"), rows), ""]
        else:
            out += [*rows, ""]
    insights = [x for x in data.get("insights") or [] if isinstance(x, dict) and text(x.get("title"))]
    if insights:
        out += [f"## {t(lang, 'pain_h')}", ""]
        for k, x in enumerate(insights, 1):
            out += [f"### {k}. {esc(x.get('title'))}", ""]
            if text(x.get("what")):
                out += [esc(cut(x.get("what"))), ""]
            if text(x.get("try")):
                out += [f"**{t(lang, 'try')}** {esc(cut(x.get('try')))}", ""]
    if themes:
        out += [f"## {t(lang, 'themes_h')}", "", t(lang, "legend"), ""]
        for th in themes:
            done, n = counted(th)
            head = " \u00b7 ".join(p for p in (text(th.get("title")), tokens(th.get("tokens")),
                                                t(lang, "done_of", d=done, n=n) if n else "") if p)
            lines = []
            for i in th.get("items") or []:
                if not isinstance(i, dict) or not text(i.get("title")):
                    continue
                line = f"- {STATES.get(i.get('state'), STATES['progress'])} **{esc(i.get('title'))}**"
                if text(i.get("tokens")):
                    line += f" \u00b7 {esc(tokens(i.get('tokens')))}."
                else:
                    line += "."
                if text(i.get("story")) and level < 2:
                    line += f" {esc(sentence(cut(i.get('story'))))}"
                if i.get("state") == "done" and i.get("first_try"):
                    line += f" {t(lang, 'first')}"
                elif i.get("state") != "done" and text(i.get("next")) and level < 1:
                    line += f" _{t(lang, 'next')} {esc(sentence(i.get('next')))}_"
                lines.append(line)
            out += [*details(head, lines), ""] if level < 3 else [f"- {esc(head)}", ""]
    together = data.get("together") if isinstance(data.get("together"), dict) else {}
    if any(together.get(k) for k in ("good", "slow")):
        out += [f"## {t(lang, 'together_h')}", ""]
        for key in ("good", "slow"):
            pts = [esc(cut(x)) for x in together.get(key) or [] if text(x)]
            if pts:
                out += [f"**{t(lang, key)}**", *[f"- {p}" for p in pts], ""]
    todo = data.get("todo") if isinstance(data.get("todo"), dict) else {}
    filed = [x for x in todo.get("filed") or [] if isinstance(x, dict) and text(x.get("title"))]
    loose = [x for x in todo.get("not_filed") or [] if isinstance(x, dict) and text(x.get("title"))]
    if filed or loose:
        out += [f"## {t(lang, 'todo_h')}", ""]
    if filed:
        out += [f"**{t(lang, 'filed')}**"]
        for x in filed:
            link = text(x.get("link"))
            label = esc(x.get("title")).replace("[", "(").replace("]", ")")
            out.append(f"- [{label}]({link})" if re.match(r"^https?://\S+$", link) else f"- {label}")
            if text(x.get("due")):
                out[-1] += f" \u00b7 {esc(x.get('due'))}"
        out.append("")
    if loose:
        rows = []
        for x in loose:
            name = esc(text(x.get("title")).rstrip(".") + ".")
            rows.append(f"- **{name}**" + (f" {esc(sentence(cut(x.get('why'))))}" if text(x.get("why")) else ""))
        out += [*details(f"{t(lang, 'not_filed')} \u00b7 {len(loose)}", rows), ""]
    return "\n".join(out).rstrip() + "\n"


def build(data, lang, folder, title):
    """Write the markdown (and the pictures) into `folder`. -> (path of the .md, [paths of the pictures])."""
    lang = lang if lang in STR else "en"
    os.makedirs(folder, exist_ok=True)
    imgs = pictures(folder, lang, data)
    for level in range(4):
        md = build_md(data, lang, imgs, title, level)
        if len(md) <= LIMIT:
            break
    path = os.path.join(folder, MD)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path, list(imgs.values())
