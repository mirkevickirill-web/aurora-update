#!/usr/bin/env python3
"""Свежие подборки для «Авроры»: новинки и популярные фильмы последних полутора лет.

Источники: Wikidata (список фильмов, жанры, страна, ID Кинопоиска), rating.kinopoisk.ru
(рейтинги и число голосов), Википедия (короткое описание), YouTube (трейлер).
Результат — films.json в корне репозитория.
"""
import datetime
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "AuroraHome-fresh/1.0 (weekly film list for a personal TV launcher)"

GENRES = [
    ("comedy", ["комеди"]),
    ("thriller", ["триллер", "детектив", "ужас", "криминал", "нуар", "хоррор", "мистик"]),
    ("scifi", ["фантаст", "фэнтези", "научн"]),
    ("adventure", ["приключ", "боевик", "экшн", "вестерн", "военн", "супергер"]),
    ("family", ["семейн", "мульт", "анимац", "детск", "мюзикл"]),
    ("soul", ["драм", "мелодрам", "биограф", "романт", "истори"]),
]


def get(url, headers=None, timeout=40):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1))
                continue
            raise


def candidates():
    """Лёгкий запрос к Wikidata, затем названия, жанры и статьи — пачками через API."""
    since = (datetime.date.today() - datetime.timedelta(days=540)).isoformat()
    q = f"""
SELECT ?film ?kp ?date WHERE {{
  ?film wdt:P2603 ?kp; wdt:P577 ?date.
  FILTER(?date >= "{since}T00:00:00Z"^^xsd:dateTime && ?date <= NOW())
  ?film wdt:P31 wd:Q11424.
  FILTER NOT EXISTS {{ ?film wdt:P577 ?older. FILTER(?older < "{since}T00:00:00Z"^^xsd:dateTime) }}
}} LIMIT 3000"""
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    t0 = time.time()
    _, _, body = get(url, headers={"Accept": "application/sparql-results+json"}, timeout=70)
    base = {}
    for b in json.loads(body)["results"]["bindings"]:
        qid = b["film"]["value"].rsplit("/", 1)[-1]
        date = b["date"]["value"][:10]
        if qid in base and base[qid]["date"] <= date:
            continue
        base[qid] = {"qid": qid, "kp": b["kp"]["value"], "date": date, "year": int(date[:4])}
    print("SPARQL:", len(base), "фильмов за", round(time.time() - t0), "с", flush=True)

    # Подробности: метки, жанры, страна, статья в ру-Википедии
    qids = list(base)
    genre_ids, country_ids = set(), set()
    for i in range(0, len(qids), 50):
        part = qids[i:i + 50]
        qs = urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(part), "format": "json",
                                     "props": "labels|claims|sitelinks", "languages": "ru", "sitefilter": "ruwiki"})
        _, _, body = get("https://www.wikidata.org/w/api.php?" + qs)
        for qid, e in json.loads(body).get("entities", {}).items():
            rec = base.get(qid)
            if not rec:
                continue
            rec["title"] = e.get("labels", {}).get("ru", {}).get("value")
            link = e.get("sitelinks", {}).get("ruwiki")
            rec["article"] = "https://ru.wikipedia.org/wiki/" + link["title"].replace(" ", "_") if link else None
            cl = e.get("claims", {})

            def ids(p):
                out = []
                for c in cl.get(p, []):
                    try:
                        out.append(c["mainsnak"]["datavalue"]["value"]["id"])
                    except Exception:
                        pass
                return out
            rec["genre_ids"] = ids("P136")
            rec["country_ids"] = ids("P495")[:1]
            genre_ids.update(rec["genre_ids"])
            country_ids.update(rec["country_ids"])
    names = {}
    allids = list(genre_ids | country_ids)
    for i in range(0, len(allids), 50):
        qs = urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(allids[i:i + 50]),
                                     "format": "json", "props": "labels", "languages": "ru"})
        _, _, body = get("https://www.wikidata.org/w/api.php?" + qs)
        for qid, e in json.loads(body).get("entities", {}).items():
            names[qid] = e.get("labels", {}).get("ru", {}).get("value", "")
    out = []
    for rec in base.values():
        if not rec.get("title"):
            continue
        rec["genres"] = "|".join(names.get(g, "") for g in rec.get("genre_ids", []))
        rec["country"] = names.get(rec["country_ids"][0], "") if rec.get("country_ids") else ""
        out.append(rec)
    print("с русским названием:", len(out), "за", round(time.time() - t0), "с", flush=True)
    return out


def rating(kp):
    try:
        req = urllib.request.Request(f"https://rating.kinopoisk.ru/{kp}.xml", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
        root = ET.fromstring(body)
        kpr = root.find("kp_rating")
        imdb = root.find("imdb_rating")
        return (float(kpr.text) if kpr is not None and kpr.text else 0.0,
                int(kpr.get("num_vote", "0")) if kpr is not None else 0,
                float(imdb.text) if imdb is not None and imdb.text else 0.0)
    except Exception:
        return 0.0, 0, 0.0


def tags_for(genres):
    """Жанры в порядке Wikidata → наши настроения (первый — основной)."""
    tags = []
    for name in genres.lower().split("|"):
        for mood, keys in GENRES:
            if any(k in name for k in keys) and mood not in tags:
                tags.append(mood)
    return tags or ["soul"]


def poster(kp):
    url = f"https://kinopoiskapiunofficial.tech/images/posters/kp/{kp}.jpg"
    try:
        st, ct, body = get(url, timeout=15)
        if st == 200 and ct.startswith("image") and len(body) > 3000:
            return url
    except Exception:
        pass
    return None


def trailer(title, year):
    q = urllib.parse.quote(f"{title} {year} трейлер на русском")
    try:
        _, _, body = get("https://www.youtube.com/results?search_query=" + q,
                         headers={"Accept-Language": "ru-RU,ru;q=0.9", "Cookie": "CONSENT=YES+1"}, timeout=20)
        m = re.search(rb'"videoId":"([A-Za-z0-9_-]{11})"', body)
        return m.group(1).decode() if m else None
    except Exception:
        return None


def description(article, year, country):
    if article:
        title = urllib.parse.unquote(article.rsplit("/", 1)[-1])
        try:
            _, _, body = get("https://ru.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title), timeout=20)
            text = json.loads(body).get("extract", "")
            text = text.replace("\u0301", "")                  # знаки ударения
            text = re.sub(r"\s*\([^()]*\)", "", text)          # убрать скобки с английскими названиями
            text = re.sub(r"\s+", " ", text).strip()
            out = ""
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if len(out) + len(sent) > 230:
                    break
                out = (out + " " + sent).strip()
            if out:
                return out, "wikipedia"
        except Exception:
            pass
    where = f", {country}" if country else ""
    return f"Фильм {year} года{where}.", None


def main():
    films = candidates()
    # фильмы со статьёй в русской Википедии обычно заметнее — берём их в первую очередь
    films.sort(key=lambda f: f["date"], reverse=True)
    films = [f for f in films if f["article"]][:700] or films[:700]
    print("кандидатов:", len(films), flush=True)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=12) as pool:
        for f, r in zip(films, pool.map(lambda x: rating(x["kp"]), films)):
            f["kpr"], f["votes"], f["imdbr"] = r
    good = [f for f in films if f["votes"] >= 3000 and f["kpr"] >= 6.0]
    print("с рейтингом:", len(good), flush=True)

    new = sorted([f for f in good if f["kpr"] >= 6.5], key=lambda f: f["date"], reverse=True)[:20]
    popular = sorted(good, key=lambda f: f["votes"], reverse=True)[:20]
    best = sorted([f for f in good if f["votes"] >= 10000],
                  key=lambda f: f["kpr"] + math.log10(f["votes"]) * 0.1, reverse=True)[:20]

    chosen = {}
    for lst in (new, popular, best):
        for f in lst:
            chosen[f["kp"]] = f
    out_films = []
    for f in chosen.values():
        desc, src = description(f["article"], f["year"], f["country"])
        rec = {
            "id": f'{f["title"]}|{f["year"]}', "title": f["title"].replace("\u0301", ""), "year": f["year"],
            "country": f["country"], "desc": desc, "tags": tags_for(f["genres"]),
            "kp": f["kp"], "kpr": round(f["kpr"], 1), "imdbr": f["imdbr"],
        }
        if src:
            rec["descSource"] = src
        p = poster(f["kp"])
        if p:
            rec["poster"] = p
        t = trailer(f["title"], f["year"])
        if t:
            rec["trailer"] = t
        out_films.append(rec)
        print(json.dumps(rec, ensure_ascii=False)[:160], flush=True)

    def ids(lst):
        return [f'{f["title"]}|{f["year"]}' for f in lst]

    result = {
        "updated": datetime.date.today().isoformat(),
        "rows": [
            {"title": "🆕 Новинки", "ids": ids(new)},
            {"title": "🔥 Популярное", "ids": ids(popular)},
            {"title": "🏆 Лучшее за полтора года", "ids": ids(best)},
        ],
        "films": out_films,
    }
    with open("films.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("готово:", len(out_films), "фильмов", flush=True)


if __name__ == "__main__":
    main()
