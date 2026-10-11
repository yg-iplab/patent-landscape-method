# -*- coding: utf-8 -*-
"""
書籍・雑誌・論文の候補リストを作る（method/04_非特許情報.md）。

  - 国立国会図書館サーチ（NDLサーチ）OpenSearch API：書籍・雑誌（号）
  - J-STAGE WebAPI（記事検索 service=3）：論文・学会発表。所属（affil）で会社に絞れる
  （CiNii Research は別途、画面か API で検索する）

使い方（例）：
  python3 biblio_search.py --company "{会社名}" --alias "{略称}" \
      --out ./work/{会社名}/reading/candidates.md

決まり（2026-10-09 確認）：
  - NDLサーチ：個人・非営利で収益を得ない利用は申請不要。APIを使っていることを明記する（出力の先頭に書く）。
    同時に何本も投げない。1回ごとに --wait 秒空ける
  - J-STAGE WebAPI：キー不要。1回の取得は最大1,000件だが、ここでは --max 件まで
  - 結果は「候補」。読む価値（A/B/C）は人またはAIが本文を見てつける
"""

import argparse
import datetime as dt
import html
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List

NDL_URL = "https://ndlsearch.ndl.go.jp/api/opensearch"
JSTAGE_URL = "https://api.jstage.jst.go.jp/searchapi/do"
UA = "ipl-method/0.1 (personal non-commercial research)"

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "atom": "http://www.w3.org/2005/Atom",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
}

# NDLサーチで会社名と組み合わせる語
NDL_TOPICS = ["知的財産", "特許", "技術戦略", "研究開発", "IPランドスケープ"]
# J-STAGE（所属＝会社）で組み合わせる語。空文字＝所属だけ
JSTAGE_KEYWORDS = ["特許", "知的財産", "知財", ""]


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def clean(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("|", "／")


def ndl_search(query: str, year_from: int, cnt: int) -> List[Dict[str, str]]:
    params = {"any": query, "cnt": str(cnt), "from": str(year_from)}
    url = NDL_URL + "?" + urllib.parse.urlencode(params)
    root = ET.fromstring(fetch(url))
    items = []
    for it in root.iter("item"):
        cats = [c.text or "" for c in it.findall("category")]
        kind = next((c for c in cats if c not in ("紙", "電子", "デジタル")), "")
        title = clean(it.findtext("dc:title", default="", namespaces=NS) or it.findtext("title", default=""))
        vol = clean(it.findtext("dcndl:volume", default="", namespaces=NS))
        issued = clean(it.findtext("dcterms:issued", default="", namespaces=NS))
        creator = clean(it.findtext("dc:creator", default="", namespaces=NS))
        publisher = clean(it.findtext("dc:publisher", default="", namespaces=NS))
        link = (it.findtext("link") or "").strip()
        items.append({
            "source": "NDL",
            "kind": kind or "不明",
            "title": title + (f"（{vol}）" if vol else ""),
            "where": publisher,
            "year": issued[:4],
            "author": creator,
            "link": link,
            "query": query,
        })
    return items


def jstage_search(affil: str, keyword: str, year_from: int, cnt: int) -> List[Dict[str, str]]:
    params = {"service": "3", "affil": affil, "pubyearfrom": str(year_from), "count": str(cnt)}
    if keyword:
        params["keyword"] = keyword
    url = JSTAGE_URL + "?" + urllib.parse.urlencode(params)
    root = ET.fromstring(fetch(url))
    status = root.findtext("atom:result/atom:status", default="", namespaces=NS) or root.findtext("result/status", default="")
    if status == "ERR_001":
        return []  # 該当なし（J-STAGE は0件のとき ERR_001 を返す）
    if status and status != "0":
        print(f"⚠ J-STAGE status={status}（{affil} {keyword}）", file=sys.stderr)
        return []
    items = []
    for e in root.findall("atom:entry", NS):
        title = clean(e.findtext("atom:article_title/atom:ja", default="", namespaces=NS)) or \
            clean(e.findtext("atom:article_title/atom:en", default="", namespaces=NS))
        journal = clean(e.findtext("atom:material_title/atom:ja", default="", namespaces=NS)) or \
            clean(e.findtext("atom:material_title/atom:en", default="", namespaces=NS))
        year = clean(e.findtext("atom:pubyear", default="", namespaces=NS))
        names = [clean(n.text or "") for n in e.findall("atom:author/atom:ja/atom:name", NS)]
        link = clean(e.findtext("atom:article_link/atom:ja", default="", namespaces=NS)) or \
            clean(e.findtext("atom:article_link/atom:en", default="", namespaces=NS))
        items.append({
            "source": "J-STAGE",
            "kind": "論文・発表",
            "title": title,
            "where": journal,
            "year": year,
            "author": f"{len(names)}名" if names else "",
            "link": link,
            "query": f"所属={affil}" + (f"・{keyword}" if keyword else ""),
        })
    return [it for it in items if it["title"] or it["link"]]


def main() -> int:
    ap = argparse.ArgumentParser(description="書籍・雑誌・論文の候補リスト（NDLサーチ・J-STAGE）")
    ap.add_argument("--company", required=True, help="会社名（正式名）")
    ap.add_argument("--alias", action="append", default=[], help="別表記（略称・旧社名など）。複数可")
    ap.add_argument("--years", type=int, default=10, help="何年前から（既定10年）")
    ap.add_argument("--max", type=int, default=20, help="1つの検索で取る件数の上限")
    ap.add_argument("--wait", type=float, default=1.0, help="検索の間隔（秒）")
    ap.add_argument("--out", required=True, help="出力する Markdown のパス（例：./work/{会社名}/reading/candidates.md）")
    args = ap.parse_args()

    year_from = dt.date.today().year - args.years
    names = [args.company] + [a for a in args.alias if a and a != args.company]
    found: List[Dict[str, str]] = []
    log: List[str] = []

    for name in names:
        for topic in NDL_TOPICS:
            q = f"{name} {topic}"
            try:
                items = ndl_search(q, year_from, args.max)
                found += items
                log.append(f"NDL any=「{q}」 from={year_from} → {len(items)}件")
            except Exception as ex:  # 通信エラーは記録して続ける
                log.append(f"NDL any=「{q}」 → エラー {ex}")
            time.sleep(args.wait)
        for kw in JSTAGE_KEYWORDS:
            try:
                items = jstage_search(name, kw, year_from, args.max)
                found += items
                log.append(f"J-STAGE affil=「{name}」 keyword=「{kw}」 from={year_from} → {len(items)}件")
            except Exception as ex:
                log.append(f"J-STAGE affil=「{name}」 keyword=「{kw}」 → エラー {ex}")
            time.sleep(args.wait)

    # 同じ資料を1行にまとめる（リンクで判定）。どの検索で出たかは残す
    merged: Dict[str, Dict[str, str]] = {}
    for it in found:
        key = it["link"] or (it["title"] + it["year"])
        if key in merged:
            if it["query"] not in merged[key]["query"]:
                merged[key]["query"] += "／" + it["query"]
        else:
            merged[key] = dict(it)
    # J-STAGE（会社の人が書いたもの）を先に、それぞれ新しい順
    rows = list(merged.values())
    rows = sorted([r for r in rows if r["source"] == "J-STAGE"], key=lambda r: r["year"], reverse=True) + \
        sorted([r for r in rows if r["source"] != "J-STAGE"], key=lambda r: r["year"], reverse=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 文献の候補：{args.company}",
        "",
        f"- 作成：{dt.date.today().isoformat()}（`biblio_search.py`）。対象：{year_from}年以降",
        "- 出典：国立国会図書館サーチのAPIを利用（https://ndlsearch.ndl.go.jp/help/api）／J-STAGE WebAPI",
        "- **これは候補。** 優先度（A＝その会社の知財・研究の人が書いたもの、経営者インタビュー／B＝業界誌・技報・特集／C＝古い・関係が薄い）は本文を見てつける",
        "- NDLの雑誌は「号」単位で出る（目次に語が含まれる号）。記事名は号の目次で確かめる",
        "- J-STAGE は所属で絞った結果。著者の個人名は出さず人数だけにしている",
        "",
        "| 優先 | 出どころ | 種類 | タイトル | 誌名・出版者 | 年 | 著者 | どの検索で | リンク |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"|  | {r['source']} | {r['kind']} | {r['title'][:80]} | {r['where'][:40]} | {r['year']} | {r['author'][:30]} | {r['query']} | {r['link']} |")
    lines += ["", "## 検索ログ", ""] + [f"- {l}" for l in log]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"書き出し：{out}（{len(rows)}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
