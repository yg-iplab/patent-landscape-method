# -*- coding: utf-8 -*-
"""
EDINET（有価証券報告書）を取る（method/04_非特許情報.md）。

  find    … 期間内の書類一覧から、会社の有価証券報告書（docTypeCode 120／訂正 130）を探す
  get     … docID を指定して PDF（type=2）か CSV（type=5、XBRLを表にしたもの）を保存する
  extract … CSV から「セグメント」「研究開発費」「売上」「利益」の行を拾って Markdown の表にする

使い方（例）：
  python3 edinet_fetch.py find --name "{会社名}" --from YYYY-MM-DD --to YYYY-MM-DD
  python3 edinet_fetch.py get --doc-id S100XXXX --type 2 --out ./work/{会社名}/data/raw
  python3 edinet_fetch.py get --doc-id S100XXXX --type 5 --out ./work/{会社名}/data/raw
  python3 edinet_fetch.py extract --csv-dir ./work/{会社名}/data/raw/S100XXXX_csv \
      --out ./work/{会社名}/data/edinet_extract.md
  （--wait と --env はサブコマンド〔find／get／extract〕の前に置く）

APIキー：
  - 無料。発行は利用者本人（https://api.edinet-fsa.go.jp/WEEE0090.aspx）
  - 環境変数 EDINET_API_KEY、またはカレントディレクトリの ./.env の EDINET_API_KEY=… を読む
  - .env は git に入れない（.gitignore 済み）
  - キーを画面・ログ・成果物に出さない

仕様（2026-10-09 時点）：
  - ベースURL https://api.edinet-fsa.go.jp/api/v2（2024年4月から version 2）
  - キーはクエリの Subscription-Key。誤ったキーでも HTTP 200 で本文に {"StatusCode": 401, ...} が返る
    （2026-10-09 にキーなしで確認）→ 本文の StatusCode を必ず見る
  - ⚠ 仕様書（2025年10月版）の細部は、キー発行後の初回実行で確かめる
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

BASE = "https://api.edinet-fsa.go.jp/api/v2"
DEFAULT_ENV = Path(".env")  # カレントディレクトリの .env
DOC_TYPES = {"120": "有価証券報告書", "130": "訂正有価証券報告書"}


def load_key(env_path: Path) -> str:
    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("EDINET_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit(
            "EDINET_API_KEY がありません。https://api.edinet-fsa.go.jp/WEEE0090.aspx で無料登録し、"
            f"環境変数 EDINET_API_KEY か {env_path} の EDINET_API_KEY=… に設定してください（.env は git に入れないこと）。"
        )
    return key


def call(path: str, params: Dict[str, str], key: str) -> bytes:
    q = dict(params)
    q["Subscription-Key"] = key
    url = f"{BASE}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ipl-method/0.1 (personal non-commercial research)"})
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read()
        ctype = res.headers.get("Content-Type", "")
    if "json" in ctype or body[:1] == b"{":
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict):
            code = str(data.get("StatusCode") or data.get("metadata", {}).get("status") or "200")
            if code not in ("200",):
                msg = data.get("message") or data.get("metadata", {}).get("message")
                raise SystemExit(f"EDINET がエラーを返しました：StatusCode={code} {msg}")
    return body


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def cmd_find(args: argparse.Namespace) -> int:
    key = load_key(Path(args.env))
    start = dt.date.fromisoformat(args.date_from)
    end = dt.date.fromisoformat(args.date_to)
    if (end - start).days > 62:
        raise SystemExit("期間は2か月以内にしてください（1日1回ずつ問い合わせるため）")
    want = nfkc(args.name)
    hits: List[Dict[str, str]] = []
    d = start
    while d <= end:
        body = call("/documents.json", {"date": d.isoformat(), "type": "2"}, key)
        data = json.loads(body.decode("utf-8"))
        for r in data.get("results", []) or []:
            if str(r.get("docTypeCode")) not in DOC_TYPES:
                continue
            name_ok = want and want in nfkc(r.get("filerName", ""))
            code_ok = args.sec_code and str(r.get("secCode") or "").startswith(args.sec_code)
            if name_ok or code_ok:
                hits.append({
                    "date": d.isoformat(),
                    "docID": r.get("docID", ""),
                    "filerName": r.get("filerName", ""),
                    "secCode": str(r.get("secCode") or ""),
                    "docType": DOC_TYPES[str(r.get("docTypeCode"))],
                    "description": r.get("docDescription", ""),
                    "periodEnd": r.get("periodEnd", ""),
                    "pdf": str(r.get("pdfFlag", "")),
                    "csv": str(r.get("csvFlag", "")),
                })
        d += dt.timedelta(days=1)
        time.sleep(args.wait)
    if not hits:
        print("見つかりませんでした。期間（3月決算なら6月中旬〜下旬）か社名の表記を見直してください。")
        return 1
    print("| 提出日 | docID | 提出者 | 証券コード | 種類 | 期末 | PDF | CSV | 説明 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for h in hits:
        print(f"| {h['date']} | {h['docID']} | {h['filerName']} | {h['secCode']} | {h['docType']} | {h['periodEnd']} | {h['pdf']} | {h['csv']} | {h['description']} |")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    key = load_key(Path(args.env))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    body = call(f"/documents/{args.doc_id}", {"type": args.type}, key)
    if args.type == "2":
        p = out / f"{args.doc_id}.pdf"
        p.write_bytes(body)
        print(f"保存：{p}（PDF。文字は scripts/pdf_text.swift で取り出せる）")
    else:
        zp = out / f"{args.doc_id}_type{args.type}.zip"
        zp.write_bytes(body)
        dest = out / f"{args.doc_id}_csv" if args.type == "5" else out / f"{args.doc_id}_xbrl"
        dest.mkdir(exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            for m in z.namelist():
                # 安全のため、フォルダの外へ出る名前は展開しない
                target = (dest / m).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue
                z.extract(m, dest)
        print(f"保存：{zp} → 展開 {dest}")
    return 0


def read_edinet_csv(path: Path) -> List[List[str]]:
    raw = path.read_bytes()
    for enc in ("utf-16", "utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    delim = "\t" if text.count("\t") > text.count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delim))


PATTERNS = [
    ("研究開発費", re.compile(r"研究開発")),
    ("セグメント", re.compile(r"セグメント|Segment")),
    ("売上", re.compile(r"売上高|売上収益|NetSales|Revenue")),
    ("利益", re.compile(r"営業利益|セグメント利益|OperatingIncome|OperatingProfit|SegmentProfit")),
    ("従業員", re.compile(r"従業員数")),
]


def cmd_extract(args: argparse.Namespace) -> int:
    root = Path(args.csv_dir)
    files = sorted(root.rglob("*.csv"))
    if not files:
        raise SystemExit(f"CSV がありません：{root}")
    lines = [f"# EDINET CSV から拾った行（{root.name}）", "",
             "- 機械的に拾った候補。**表に使う数字は、有報PDFの該当頁で必ず確かめる**",
             "- 列：ファイル／分類／要素ID／項目名／コンテキストID／相対年度／連結・個別／値", ""]
    count = 0
    for f in files:
        rows = read_edinet_csv(f)
        if not rows:
            continue
        header = rows[0]
        idx = {h: i for i, h in enumerate(header)}
        def col(r: List[str], name: str) -> str:
            i = idx.get(name)
            return r[i] if i is not None and i < len(r) else ""
        body = []
        for r in rows[1:]:
            item = col(r, "項目名")
            elem = col(r, "要素ID")
            ctx = col(r, "コンテキストID")
            text = f"{elem} {item} {ctx}"
            for label, pat in PATTERNS:
                if pat.search(text):
                    val = col(r, "値")
                    if len(val) > 60:
                        val = val[:60] + "…"
                    body.append(f"| {f.name} | {label} | {elem} | {item} | {ctx} | {col(r, '相対年度')} | {col(r, '連結・個別')} | {val} |")
                    break
        if body:
            lines += [f"## {f.name}", "", "| ファイル | 分類 | 要素ID | 項目名 | コンテキストID | 相対年度 | 連結・個別 | 値 |",
                      "|---|---|---|---|---|---|---|---|"] + body[: args.limit] + [""]
            count += len(body)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"書き出し：{out}（候補 {count} 行）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="EDINET（有価証券報告書）を取る")
    ap.add_argument("--env", default=str(DEFAULT_ENV), help=f"APIキーの .env（既定 {DEFAULT_ENV}。環境変数 EDINET_API_KEY が先に使われる）")
    ap.add_argument("--wait", type=float, default=1.0, help="問い合わせの間隔（秒）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="期間内の有価証券報告書を探す")
    f.add_argument("--name", default="", help="提出者名（一部でよい）")
    f.add_argument("--sec-code", default="", help="証券コード（例 6367）")
    f.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    f.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    f.set_defaults(func=cmd_find)

    g = sub.add_parser("get", help="docID の書類を保存する")
    g.add_argument("--doc-id", required=True)
    g.add_argument("--type", choices=["1", "2", "5"], default="2", help="1=XBRL一式 2=PDF 5=CSV")
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_get)

    e = sub.add_parser("extract", help="CSV からセグメント・研究開発費などの行を拾う")
    e.add_argument("--csv-dir", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--limit", type=int, default=200, help="1ファイルあたりの最大行数")
    e.set_defaults(func=cmd_extract)

    args = ap.parse_args()
    if args.cmd == "find" and not (args.name or args.sec_code):
        raise SystemExit("--name か --sec-code のどちらかを指定してください")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
