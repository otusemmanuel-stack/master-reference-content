#!/usr/bin/env python3
"""
Builds the Talk Assistant Conference packs by itself, after each General Conference.

What it does, in order:
  1. Looks for the latest General Conferences on the Church's official website.
  2. For every talk the packs do not have yet, reads the talk page (English and French).
  3. Writes one small pack file per talk and per language into the "talk" folder.
  4. Updates talk/manifest.json, newest Conference first.

Rules it follows:
  * Quotes are word for word from the talk, with the speaker, talk title and session.
    Nothing is rewritten or paraphrased, so nothing can be wrong.
  * Scriptures are listed by reference, and the verse words are copied from the scripture
    files in tools/scriptures (the same files the app uses), never typed by hand.
  * Nothing is deleted. Packs that already exist are left alone.
Run by GitHub every day in April and October (see .github/workflows/talk-packs.yml).
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.churchofjesuschrist.org"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "talk")
DATA = os.path.join(ROOT, "tools", "scriptures")
HEADERS = {"User-Agent": "MasterReferenceTalkPacks/1.0 (study app, builds packs twice a year)"}
LANGS = {"en": "eng", "fr": "fra"}
MONTH_EN = {4: "April", 10: "October"}
MONTH_FR = {4: "avril", 10: "octobre"}
SKIP_TITLE_WORDS = ["sustaining", "auditing", "statistical", "soutien", "vérification", "statistique"]

# Which topic pack a talk belongs to is decided by these words in its title or text.
# Add a line here each time a new topic pack is added to the "talk" folder.
TOPIC_KEYWORDS = {
    "testimony": ["testimony", "témoignage"],
}

# Church website book names -> names used in the scripture files.
BOOKS = {
    "ot": {"gen": "Genesis", "ex": "Exodus", "lev": "Leviticus", "num": "Numbers", "deut": "Deuteronomy",
           "josh": "Joshua", "judg": "Judges", "ruth": "Ruth", "1-sam": "1 Samuel", "2-sam": "2 Samuel",
           "1-kgs": "1 Kings", "2-kgs": "2 Kings", "1-chr": "1 Chronicles", "2-chr": "2 Chronicles",
           "ezra": "Ezra", "neh": "Nehemiah", "esth": "Esther", "job": "Job", "ps": "Psalms",
           "prov": "Proverbs", "eccl": "Ecclesiastes", "song": "Song of Solomon", "isa": "Isaiah",
           "jer": "Jeremiah", "lam": "Lamentations", "ezek": "Ezekiel", "dan": "Daniel", "hosea": "Hosea",
           "joel": "Joel", "amos": "Amos", "obad": "Obadiah", "jonah": "Jonah", "micah": "Micah",
           "nahum": "Nahum", "hab": "Habakkuk", "zeph": "Zephaniah", "hag": "Haggai", "zech": "Zechariah",
           "mal": "Malachi"},
    "nt": {"matt": "Matthew", "mark": "Mark", "luke": "Luke", "john": "John", "acts": "Acts", "rom": "Romans",
           "1-cor": "1 Corinthians", "2-cor": "2 Corinthians", "gal": "Galatians", "eph": "Ephesians",
           "philip": "Philippians", "col": "Colossians", "1-thes": "1 Thessalonians",
           "2-thes": "2 Thessalonians", "1-tim": "1 Timothy", "2-tim": "2 Timothy", "titus": "Titus",
           "philem": "Philemon", "heb": "Hebrews", "james": "James", "1-pet": "1 Peter", "2-pet": "2 Peter",
           "1-jn": "1 John", "2-jn": "2 John", "3-jn": "3 John", "jude": "Jude", "rev": "Revelation"},
    "bofm": {"1-ne": "1 Nephi", "2-ne": "2 Nephi", "jacob": "Jacob", "enos": "Enos", "jarom": "Jarom",
             "omni": "Omni", "w-of-m": "Words of Mormon", "mosiah": "Mosiah", "alma": "Alma",
             "hel": "Helaman", "3-ne": "3 Nephi", "4-ne": "4 Nephi", "morm": "Mormon", "ether": "Ether",
             "moro": "Moroni"},
    "dc-testament": {"dc": "D&C"},
    "pgp": {"moses": "Moses", "abr": "Abraham", "js-m": "Joseph Smith-Matthew",
            "js-h": "Joseph Smith-History", "a-of-f": "Articles of Faith"},
}
STANDARD_WORKS = {"bofm", "dc-testament", "pgp"}

_session = requests.Session()
_session.headers.update(HEADERS)
_scripture_cache = {}


def fetch(url):
    """Downloads a page politely. Returns the text, or None when the page does not exist."""
    for attempt in range(3):
        try:
            time.sleep(1.0)
            r = _session.get(url, timeout=40)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.content.decode("utf-8", errors="replace")
        except requests.RequestException:
            pass
        time.sleep(3 * (attempt + 1))
    return None


def scripture_file(lang, standard):
    name = {("en", True): "lds_scriptures.json", ("fr", True): "lds_scriptures_fr.json",
            ("en", False): "kjv_bible.json", ("fr", False): "lsg_bible.json"}[(lang, standard)]
    key = name
    if key not in _scripture_cache:
        path = os.path.join(DATA, name)
        _scripture_cache[key] = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    return _scripture_cache[key]


def verse_words(lang, standard, book, chapter, first, last):
    data = scripture_file(lang, standard)
    if not data:
        return None
    # The French Bible file is keyed by the English book name, like the app's own file.
    try:
        parts = [data[book][str(chapter)][str(v)] for v in range(first, last + 1)]
    except (KeyError, TypeError):
        return None
    return " ".join(parts)


def reference_from_link(href, lang):
    """Turns a scripture link from the Church website into a reference and its verse words."""
    parsed = urllib.parse.urlparse(href)
    m = re.match(r"/study/scriptures/([a-z-]+)/([a-z0-9-]+)(?:/(\d+))?", parsed.path)
    if not m:
        return None
    group, slug, chapter = m.group(1), m.group(2), m.group(3)
    book = BOOKS.get(group, {}).get(slug)
    if not book or not chapter:
        return None
    query = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
    v = re.match(r"p(\d+)(?:-p(\d+))?", query)
    if not v:
        return None
    first = int(v.group(1))
    last = int(v.group(2)) if v.group(2) else first
    if last < first or last - first > 6:
        return None
    words = verse_words(lang, group in STANDARD_WORKS, book, int(chapter), first, last)
    if not words:
        return None
    label = "Doctrine and Covenants" if book == "D&C" else book
    ref = f"{label} {chapter}:{first}" + (f"–{last}" if last != first else "")
    return {"ref": ref, "text": words}


def tidy(text):
    """Normal spaces, no space inside brackets, no space before a comma or full stop."""
    text = re.sub(r"\s+", " ", text.replace("\u00a0", " ").replace("\u202f", " ")).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.replace(" ,", ",").replace(" .", ".").strip()


def clean(p):
    for sup in p.find_all("sup"):
        sup.decompose()
    for a in p.find_all("a"):
        cls = " ".join(a.get("class", []))
        if "note-ref" in cls or "footnote" in cls:
            a.decompose()
    text = tidy(p.get_text(" ", strip=True))
    # a footnote number stuck to the last letter of a word, like "hommes8 »" or "men5."
    return re.sub(r"(?<=[A-Za-zÀ-ÿ])\d{1,2}(?=\s*[»”\"’.,;:?!)]|\s|$)", "", text)


# Personal stories (the speaker's own family events) are not offered as quotes.
FIRST_PERSON = re.compile(r"\b(i|my|me|myself|mine)\b|\b(je|moi|mon|ma|mes|me|m’|j’)\b", re.I)
SENSITIVE = re.compile(
    r"passed away|funeral|suicide|abuse|cancer|miscarriage|stillborn|divorce|"
    r"décédé|funérailles|suicide|abus|cancer|fausse couche|mort-né|divorce", re.I)


QUOTED = re.compile(r"“[^”]*”|«[^»]*»|\"[^\"]*\"")


def personal_story(p):
    own_words = QUOTED.sub(" ", p)  # words inside quotation marks belong to someone else
    singular = len(FIRST_PERSON.findall(own_words))
    return singular >= 2 or bool(SENSITIVE.search(p))


def pick_quotes(paragraphs, count=8):
    """Chooses word-for-word paragraphs spread across the whole talk."""
    def usable(p):
        if not 30 <= len(p.split()) <= 110:
            return False
        if p.lower().startswith(("amen", "i say these things")):
            return False
        return p[0].isupper() or p[0] in "“\"«"
    candidates = [p for p in paragraphs if usable(p)]
    shared = [p for p in candidates if not personal_story(p)]
    if len(shared) >= 3:
        candidates = shared
    if not candidates:
        return []
    keywords = ["jesus christ", "savior", "sauveur", "lord", "seigneur", "god", "dieu", "holy ghost",
                "saint-esprit", "spirit", "esprit", "gospel", "évangile", "covenant", "alliance"]
    def score(t):
        low = t.lower()
        return sum(low.count(k) for k in keywords)
    size = max(1, len(candidates) // count)
    chosen = []
    for i in range(0, len(candidates), size):
        bucket = candidates[i:i + size]
        best = max(bucket, key=score)
        chosen.append(best)
        if len(chosen) == count:
            break
    return chosen


def parse_talk(html, lang):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1")
    author = soup.select_one("p.author-name")
    role = soup.select_one("p.author-role")
    body = soup.select_one("div.body-block")
    if not (title and author and body):
        return None
    speaker = tidy(re.sub(r"^(By|Par)\s+", "", author.get_text(" ", strip=True)))
    paragraphs = [clean(p) for p in body.find_all("p") if p.get("data-aid") or p.get("id")]
    paragraphs = [p for p in paragraphs if p]
    refs, seen = [], set()
    for a in body.select("a.scripture-ref"):
        r = reference_from_link(a.get("href", ""), lang)
        if r and r["ref"] not in seen:
            seen.add(r["ref"])
            refs.append(r)
    return {
        "title": clean(title),
        "speaker": speaker,
        "role": tidy(role.get_text(" ", strip=True)) if role else "",
        "paragraphs": paragraphs,
        "scriptures": refs[:10],
    }


def talk_links(page_html, year, month):
    soup = BeautifulSoup(page_html, "html.parser")
    seen, result = set(), []
    for a in soup.find_all("a", href=True):
        m = re.match(rf"/study/general-conference/{year}/{month:02d}/([a-z0-9-]+)", a["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            result.append(m.group(1))
    return result


def is_session(slug):
    return slug.endswith(("-session", "-sessions")) or "session" in slug or "seance" in slug


def talks_by_index_order(index_html, year, month, code):
    """Reads the conference index page from top to bottom. Each talk belongs to the session
    heading it is listed under. Returns [(slug, session name)]."""
    soup = BeautifulSoup(index_html, "html.parser")
    pattern = rf"/study/general-conference/{year}/{month:02d}/([a-z0-9-]+)"
    current, seen, talks = None, set(), []
    for a in soup.find_all("a", href=True):
        m = re.match(pattern, a["href"])
        if not m:
            continue
        slug = m.group(1)
        if is_session(slug):
            name = clean(a)
            if not name:
                page = fetch(f"{BASE}/study/general-conference/{year}/{month:02d}/{slug}?lang={code}")
                h1 = BeautifulSoup(page, "html.parser").select_one("h1") if page else None
                name = clean(h1) if h1 else ""
            current = name or None
        elif current and slug not in seen:
            seen.add(slug)
            talks.append((slug, current))
    return talks


def talks_by_session_pages(index_html, year, month, code):
    """Older way: open every session page. Used only if the index page cannot be read in order."""
    slugs = talk_links(index_html, year, month)
    sessions = [x for x in slugs if is_session(x)]
    talks = []
    for sl in sessions:
        page = fetch(f"{BASE}/study/general-conference/{year}/{month:02d}/{sl}?lang={code}")
        if not page:
            continue
        soup = BeautifulSoup(page, "html.parser")
        name = clean(soup.select_one("h1")) if soup.select_one("h1") else sl
        for slug in talk_links(page, year, month):
            if slug not in sessions and slug not in [t[0] for t in talks]:
                talks.append((slug, name))
    return talks


def conference_talks(year, month, lang):
    """Returns [(slug, session name)] in the order of the conference, or None if it is not out yet."""
    code = LANGS[lang]
    index = fetch(f"{BASE}/study/general-conference/{year}/{month:02d}?lang={code}")
    if not index:
        return None
    talks = talks_by_index_order(index, year, month, code)
    n_sessions = len({t[1] for t in talks})
    if len(talks) < 8 or n_sessions < 2:
        print(f"note: index order not usable for {year}-{month:02d} {lang}, using session pages")
        talks = talks_by_session_pages(index, year, month, code)
    return talks or None


def load_manifest():
    path = os.path.join(OUT, "manifest.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {"version": 1, "packs": []}


def topic_ids(parsed, existing_topics):
    text = (parsed["title"] + " " + " ".join(parsed["paragraphs"])).lower()
    ids = []
    for topic, words in TOPIC_KEYWORDS.items():
        if topic in existing_topics and sum(text.count(w) for w in words) >= 3:
            ids.append(topic)
    return ids


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = load_manifest()
    packs = manifest["packs"]
    if os.environ.get("REBUILD") == "1":
        for p in [p for p in packs if p["kind"] == "conference"]:
            f = os.path.join(OUT, p["file"])
            if os.path.exists(f):
                os.remove(f)
        packs[:] = [p for p in packs if p["kind"] != "conference"]
        print("rebuild: old conference packs removed")
    have = {(p["kind"], p["lang"], p["id"]) for p in packs}
    existing_topics = {p["id"] for p in packs if p["kind"] == "topic"}
    today = datetime.date.today()

    # The latest conferences, newest first: this October or April, then the ones before.
    candidates = []
    y, m = today.year, 10 if today.month >= 10 else 4
    for _ in range(2):
        candidates.append((y, m))
        if m == 10:
            m = 4
        else:
            m, y = 10, y - 1

    added = 0
    for year, month in candidates:
        talks_by_lang = {lang: conference_talks(year, month, lang) for lang in LANGS}
        if not talks_by_lang["en"]:
            continue
        french = dict(talks_by_lang["fr"] or [])
        for slug, session_en in talks_by_lang["en"]:
            talk_id = f"{year}-{month:02d}-{re.sub(r'^[0-9]+', '', slug)}"
            if ("conference", "en", talk_id) in have:
                continue
            html = fetch(f"{BASE}/study/general-conference/{year}/{month:02d}/{slug}?lang=eng")
            parsed = parse_talk(html, "en") if html else None
            if not parsed or any(w in parsed["title"].lower() for w in SKIP_TITLE_WORDS):
                continue
            for lang in LANGS:
                if lang == "en":
                    data, session_name = parsed, session_en
                else:
                    page = fetch(f"{BASE}/study/general-conference/{year}/{month:02d}/{slug}?lang=fra")
                    data = parse_talk(page, "fr") if page else None
                    session_name = french.get(slug, "")
                    if not data:
                        continue
                if lang == "en":
                    session = f"{session_name}, {MONTH_EN[month]} {year} General Conference"
                else:
                    session = f"{session_name}, Conférence générale d’{MONTH_FR[month]} {year}"
                quotes = [{"text": q, "speaker": data["speaker"], "talk": data["title"], "session": session}
                          for q in pick_quotes(data["paragraphs"])]
                lead = (f"{data['speaker']} spoke about “{data['title']}”." if lang == "en"
                        else f"{data['speaker']} a parlé de « {data['title']} ».")
                pack = {
                    "id": talk_id, "lang": lang, "speaker": data["speaker"], "title": data["title"],
                    "session": session, "year": year, "month": month,
                    "topicIds": topic_ids(data, existing_topics),
                    "points": [{"text": lead}],
                    "quotes": quotes,
                    "scriptures": data["scriptures"],
                }
                file_name = f"conference-{talk_id}-{lang}.json"
                with open(os.path.join(OUT, file_name), "w", encoding="utf-8") as f:
                    json.dump(pack, f, ensure_ascii=False, indent=2)
                packs.append({"id": talk_id, "kind": "conference", "lang": lang, "version": 1,
                              "file": file_name, "title": f"{data['title']} ({data['speaker']})"})
                added += 1
                print("added", file_name)

    if added:
        # Newest conference first, then topics. Inside one conference the talks keep their order.
        def order(p):
            if p["kind"] == "conference":
                y, mo = p["id"][:4], p["id"][5:7]
                return (0, -(int(y) * 100 + int(mo)))
            return (1, 0)
        indexed = sorted(enumerate(packs), key=lambda ip: (order(ip[1]), ip[0]))
        manifest["packs"] = [p for _, p in indexed]
        manifest["version"] = manifest.get("version", 1) + 1
        with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"done, {added} new pack files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
