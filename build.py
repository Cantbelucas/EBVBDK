#!/usr/bin/env python3
"""
Statisk site-generator uden afhaengigheder.

    python build.py            byg til dist/
    python build.py --serve    byg og start lokal preview paa http://localhost:8000

Laeser:
    content/site.md            navn, rolle, tagline, mail + "Om mig"-teksten
    content/kompetencer.md     kompetencegrupper
    content/projects/*.md      ET projekt pr. fil

Skriver:
    dist/index.html + dist/assets/   -- rene filer, klar til nginx.

Et nyt projekt = en ny .md-fil i content/projects/. Ingen andre
aendringer noedvendige: nummerering, filterknapper og projekttaeller
bliver genereret.
"""

import html
import re
import shutil
import sys
from datetime import date
from hashlib import sha1
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
SRC = ROOT / "src"
DIST = ROOT / "dist"
ASSETS = DIST / "assets"

ASSET_FILES = ("styles.css", "main.js", "favicon.svg")

# Tekst i "status:" -> CSS-modifier. Ukendte vaerdier faar den neutrale.
STATUS_CLASSES = {
    "i drift": "live",
    "kører": "live",
    "aktiv": "live",
    "løbende": "ongoing",
    "igangværende": "ongoing",
    "under udvikling": "wip",
    "i gang": "wip",
    "planlagt": "wip",
    "afsluttet": "done",
    "færdig": "done",
    "arkiveret": "done",
}


# --------------------------------------------------------------------
# Markdown (den delmaengde siden faktisk bruger)
# --------------------------------------------------------------------

CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
SAFE_URL_RE = re.compile(r"^(https?:|mailto:|tel:|#|/|\./)")
PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")


def _link(match):
    label, url = match.group(1), match.group(2)
    if not SAFE_URL_RE.match(url):
        return label
    extra = ' target="_blank" rel="noopener noreferrer"' if url.startswith("http") else ""
    return '<a href="{0}"{1}>{2}</a>'.format(url, extra, label)


def inline(text):
    """Inline-formatering: `kode`, **fed**, [tekst](url). Alt andet escapes."""
    stash = []

    def keep_code(match):
        stash.append("<code>" + html.escape(match.group(1)) + "</code>")
        return "\x00{0}\x00".format(len(stash) - 1)

    text = CODE_RE.sub(keep_code, text)
    text = html.escape(text, quote=False)
    text = LINK_RE.sub(_link, text)
    text = BOLD_RE.sub(r"<strong>\1</strong>", text)
    return PLACEHOLDER_RE.sub(lambda m: stash[int(m.group(1))], text)


def markdown(body, heading="h4"):
    """Blokke: afsnit, punktlister og '## ' overskrifter."""
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [line for line in block.strip().split("\n") if line.strip()]
        if not lines:
            continue

        if all(line.lstrip().startswith("- ") for line in lines):
            items = "".join(
                "<li>{0}</li>".format(inline(line.lstrip()[2:].strip())) for line in lines
            )
            out.append("<ul>{0}</ul>".format(items))
            continue

        if lines[0].lstrip().startswith("## "):
            title = inline(lines[0].lstrip()[3:].strip())
            out.append("<{0}>{1}</{0}>".format(heading, title))
            lines = lines[1:]
            if not lines:
                continue

        out.append("<p>{0}</p>".format(inline(" ".join(l.strip() for l in lines))))
    return "\n        ".join(out)


# --------------------------------------------------------------------
# Indlaesning
# --------------------------------------------------------------------

def read_doc(path):
    """Returnerer (meta, body). Frontmatter er valgfri."""
    text = path.read_text(encoding="utf-8").lstrip("﻿").replace("\r\n", "\n")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not match:
        return {}, text
    return parse_meta(match.group(1)), match.group(2)


def parse_meta(block):
    meta = {}
    for line in block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value
    return meta


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def slug(value):
    value = value.lower().strip()
    for src_ch, dst in (("æ", "ae"), ("ø", "oe"), ("å", "aa")):
        value = value.replace(src_ch, dst)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "x"


# --------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------

def render_project(number, meta, body):
    title = meta.get("title", "Uden titel")
    status = meta.get("status", "").strip()
    tags = as_list(meta.get("tags"))
    summary = meta.get("summary", "").strip()

    parts = ['<article class="project" data-tags="{0}">'.format(
        html.escape("|".join(slug(t) for t in tags), quote=True)
    )]

    parts.append('          <div class="project__top">')
    parts.append('            <span class="project__idx" aria-hidden="true">{0:02d}</span>'.format(number))
    parts.append('            <h3 class="project__title">{0}</h3>'.format(inline(title)))
    if status:
        parts.append('            <span class="status status--{0}">{1}</span>'.format(
            STATUS_CLASSES.get(status.lower(), "done"), html.escape(status)
        ))
    parts.append("          </div>")

    if summary:
        parts.append('          <p class="project__summary">{0}</p>'.format(inline(summary)))

    if body.strip():
        parts.append('          <div class="prose project__body">\n        {0}\n          </div>'.format(
            markdown(body)
        ))

    if tags:
        items = "".join(
            '<li class="tag">{0}</li>'.format(html.escape(tag)) for tag in tags
        )
        parts.append('          <ul class="tags" aria-label="Teknologier">{0}</ul>'.format(items))

    parts.append("        </article>")
    return "\n".join(parts)


def render_filters(all_tags):
    chips = ['<button type="button" class="chip" data-filter="*" aria-pressed="true">Alle</button>']
    for tag in all_tags:
        chips.append(
            '<button type="button" class="chip" data-filter="{0}" aria-pressed="false">{1}</button>'.format(
                html.escape(slug(tag), quote=True), html.escape(tag)
            )
        )
    return "\n          ".join(chips)


def render_skills(text):
    groups, current = [], None
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            current = {"name": line[3:].strip(), "items": []}
            groups.append(current)
        elif line.startswith("- ") and current is not None:
            current["items"].append(line[2:].strip())

    blocks = []
    for group in groups:
        items = "".join("<li>{0}</li>".format(inline(i)) for i in group["items"])
        blocks.append(
            '<section class="skill-group">\n'
            "          <h3>{0}</h3>\n"
            "          <ul>{1}</ul>\n"
            "        </section>".format(inline(group["name"]), items)
        )
    return "\n        ".join(blocks)


# --------------------------------------------------------------------
# Byg
# --------------------------------------------------------------------

def build():
    site_meta, about_body = read_doc(CONTENT / "site.md")

    project_files = sorted(
        p for p in (CONTENT / "projects").glob("*.md") if not p.name.startswith("_")
    )
    if not project_files:
        sys.exit("Ingen projekter fundet i content/projects/")

    projects, tag_order = [], []
    for path in project_files:
        meta, body = read_doc(path)
        meta.setdefault("title", path.stem)
        projects.append((meta, body))
        for tag in as_list(meta.get("tags")):
            if tag not in tag_order:
                tag_order.append(tag)

    # Valgfri 'order:' i frontmatter vinder over filnavnet.
    def sort_key(item):
        try:
            return int(str(item[0].get("order", "")).strip()) or 10_000
        except ValueError:
            return 10_000

    projects.sort(key=sort_key)

    cards = "\n\n        ".join(
        render_project(i, meta, body) for i, (meta, body) in enumerate(projects, start=1)
    )

    skills_source = (CONTENT / "kompetencer.md").read_text(encoding="utf-8")

    ASSETS.mkdir(parents=True, exist_ok=True)
    versions = {}
    for name in ASSET_FILES:
        source = SRC / name
        shutil.copy2(source, ASSETS / name)
        digest = sha1(source.read_bytes()).hexdigest()[:8]
        versions[name] = "?v=" + digest

    values = {
        "NAME": html.escape(site_meta.get("name", "Dit navn")),
        "ROLE": html.escape(site_meta.get("role", "")),
        "TAGLINE": html.escape(site_meta.get("tagline", "")),
        "DESCRIPTION": html.escape(site_meta.get("description", ""), quote=True),
        "BASE": html.escape(site_meta.get("base", "")),
        "EMAIL": html.escape(site_meta.get("email", ""), quote=True),
        "SEEKING": html.escape(site_meta.get("seeking", "")),
        "PROJECT_COUNT": str(len(projects)),
        "ABOUT": markdown(about_body, heading="h3"),
        "PROJECTS": cards,
        "FILTERS": render_filters(tag_order),
        "SKILLS": render_skills(skills_source),
        "YEAR": str(date.today().year),
        "CSS_V": versions["styles.css"],
        "JS_V": versions["main.js"],
    }

    page = (SRC / "template.html").read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", value)

    left = re.findall(r"\{\{([A-Z_]+)\}\}", page)
    if left:
        sys.exit("Ikke-udfyldte pladsholdere i template.html: " + ", ".join(sorted(set(left))))

    (DIST / "index.html").write_text(page, encoding="utf-8", newline="\n")
    (DIST / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n", encoding="utf-8", newline="\n"
    )

    size = (DIST / "index.html").stat().st_size
    print("dist/index.html      {0:>6,} bytes".format(size))
    for name in ASSET_FILES:
        print("dist/assets/{0:<12} {1:>6,} bytes".format(name, (ASSETS / name).stat().st_size))
    print("{0} projekter, {1} teknologi-tags".format(len(projects), len(tag_order)))


def serve(port=8000):
    import http.server
    import socketserver

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(DIST), **kw
    )
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print("Preview: http://localhost:{0}  (Ctrl+C for at stoppe)".format(port))
        httpd.serve_forever()


if __name__ == "__main__":
    build()
    if "--serve" in sys.argv:
        serve()
