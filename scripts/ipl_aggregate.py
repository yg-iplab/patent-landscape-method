# -*- coding: utf-8 -*-
"""
企業1社の特許出願の分析：母集団の集計と図（method/03_集計と図.md）。

J-PlatPat の一覧（画面から写した results.tsv や、ログインして出力した CSV）を読み、
名寄せ表で出願人をまとめてから、次を出す。

  - 出願年ごとの件数（直近2年は「未公開分あり」と印をつける）
  - FI（技術分類）の上位と、前半・後半のシェアの変化、新しく現れた分類
  - 名寄せ後の出願人の件数と、名寄せ表に無い表記の一覧（名寄せ表を育てる材料）
  - 共同出願人（特許庁APIのCSVのように出願人が全員入っているファイルを渡したとき）
  - SVG の図（年次の棒グラフ、FIシェアの前半・後半の比較）

使い方（例）：
  python3 ipl_aggregate.py --input ./work/{会社名}/jplatpat/results.tsv \
      --name-map ./work/{会社名}/name_map.csv --out ./work/{会社名}/charts \
      --company "{会社名}" --as-of YYYY-MM-DD
  python3 ipl_aggregate.py --input a.tsv b.csv --applicants-csv patents_XXXX.csv --out ./work/{会社名}/charts

前提：
  - 標準ライブラリだけで動く（Python 3.9 以上）。matplotlib は使わない
  - 件数の単位は「出願」（出願番号で重複を除く）。日付は既定で出願日
  - 列名は自動で探す。見つからないときは --col 出願日=出願年月日 のように指定する
"""

import argparse
import csv
import datetime as dt
import io
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 列名の候補（J-PlatPat の画面一覧・CSV出力・特許庁APIのCSVの違いを吸収する）
COLUMN_ALIASES: Dict[str, Sequence[str]] = {
    "出願番号": ("出願番号", "出願番号(特許)", "出願番号（特許）"),
    "文献番号": ("文献番号", "公開番号", "公報番号", "登録番号"),
    "出願日": ("出願日", "出願年月日"),
    "公知日": ("公知日", "公開日", "発行日", "公報発行日"),
    "名称": ("発明の名称", "名称"),
    "出願人": (
        "出願人/権利者", "出願人・権利者", "出願人・権利者（筆頭）",
        "出願人/権利者（筆頭）", "出願人", "権利者",
    ),
    "ステータス": ("ステータス",),
    "FI": ("FI", "ＦＩ", "FI分類", "ＦＩ分類"),
}

# 出願人の欄で複数名を区切る記号
APPLICANT_SEPARATORS = re.compile(r"\s*(?:/|／|;|；|，|,|、|\n)\s*")
# 法人格など、名寄せのときに外す語
CORP_WORDS = re.compile(
    r"(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|"
    r"公益社団法人|公益財団法人|国立研究開発法人|独立行政法人|学校法人|国立大学法人|"
    r"\(株\)|（株）|㈱|\(有\)|（有）|ｶﾌﾞｼｷｶﾞｲｼｬ)"
)
# 大学・研究機関の見分け（共同出願人の種類分け。あくまで目安）
ACADEMIC_WORDS = re.compile(r"(大学|University|研究所|研究機構|研究開発法人|高等専門学校|高専|Institute)")


# ---------------------------------------------------------------- 読み込み

def norm_text(s: str) -> str:
    """全角・半角をそろえ、前後の空白を取る。"""
    return unicodedata.normalize("NFKC", s or "").strip()


def norm_company(s: str) -> str:
    """名寄せ用：法人格・空白・記号を外して比べやすくする。"""
    s = norm_text(s)
    s = CORP_WORDS.sub("", s)
    s = re.sub(r"[\s・\.\-ー－]", "", s)
    return s.lower()


def norm_app_number(s: str) -> str:
    """特願2024-154197 / 2024-154197 / 2024154197 を 2024154197 にそろえる。"""
    digits = re.sub(r"\D", "", norm_text(s))
    return digits


def parse_date(s: str) -> Optional[dt.date]:
    s = norm_text(s)
    m = re.search(r"(\d{4})[/\-\.年]?(\d{1,2})[/\-\.月]?(\d{1,2})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def sniff_rows(path: Path) -> List[Dict[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"文字コードを判定できませんでした：{path}")
    first = text.splitlines()[0] if text else ""
    delim = "\t" if first.count("\t") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    return [{norm_text(k): (v or "") for k, v in row.items() if k} for row in reader]


def find_column(headers: Iterable[str], key: str, overrides: Dict[str, str]) -> Optional[str]:
    headers = list(headers)
    if key in overrides:
        want = norm_text(overrides[key])
        return want if want in headers else None
    for alias in COLUMN_ALIASES.get(key, ()):
        if norm_text(alias) in headers:
            return norm_text(alias)
    return None


class Record:
    __slots__ = ("app_no", "doc_no", "filed", "published", "title", "applicants", "status", "fi_codes", "source")

    def __init__(self):
        self.app_no = ""
        self.doc_no = ""
        self.filed: Optional[dt.date] = None
        self.published: Optional[dt.date] = None
        self.title = ""
        self.applicants: List[str] = []
        self.status = ""
        self.fi_codes: List[str] = []
        self.source = ""


def split_applicants(cell: str) -> List[str]:
    cell = norm_text(cell)
    cell = re.sub(r"\s*他\s*$", "", cell)  # J-PlatPat 一覧の「〜 他」
    parts = [p for p in APPLICANT_SEPARATORS.split(cell) if p]
    return parts


def split_fi(cell: str) -> List[str]:
    cell = norm_text(cell).upper()
    # 例: "C08L23/12 C08K3/00" / "C08L23/12,101" / "B32B27/32@Z"
    codes = re.findall(r"[A-H]\d{2}[A-Z]\s?\d{1,4}/\d{1,6}", cell)
    return [c.replace(" ", "") for c in codes]


def load_records(paths: Sequence[Path], overrides: Dict[str, str]) -> Tuple[List[Record], List[str]]:
    records: List[Record] = []
    notes: List[str] = []
    for path in paths:
        rows = sniff_rows(path)
        if not rows:
            notes.append(f"{path.name}：行がありません")
            continue
        headers = rows[0].keys()
        cols = {k: find_column(headers, k, overrides) for k in COLUMN_ALIASES}
        if not cols["出願番号"] and not cols["文献番号"]:
            raise SystemExit(f"{path.name}：出願番号・文献番号の列が見つかりません。--col で指定してください。列：{list(headers)}")
        missing = [k for k in ("出願日", "出願人", "FI") if not cols[k]]
        if missing:
            notes.append(f"{path.name}：列が無いため集計しない項目 → {', '.join(missing)}")
        for row in rows:
            r = Record()
            r.source = path.name
            r.app_no = norm_app_number(row.get(cols["出願番号"] or "", ""))
            r.doc_no = norm_text(row.get(cols["文献番号"] or "", ""))
            r.filed = parse_date(row.get(cols["出願日"] or "", ""))
            r.published = parse_date(row.get(cols["公知日"] or "", ""))
            r.title = norm_text(row.get(cols["名称"] or "", ""))
            r.applicants = split_applicants(row.get(cols["出願人"] or "", ""))
            r.status = norm_text(row.get(cols["ステータス"] or "", ""))
            r.fi_codes = split_fi(row.get(cols["FI"] or "", ""))
            if r.app_no or r.doc_no:
                records.append(r)
    return records, notes


def dedupe(records: List[Record]) -> Tuple[List[Record], int]:
    """出願番号で重複を除く（同じ出願の公開公報と登録公報を1件にする）。情報が多い方を残す。"""
    best: Dict[str, Record] = {}
    for r in records:
        key = r.app_no or ("DOC:" + r.doc_no)
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        # FI・出願人・出願日が埋まっている方を優先し、空欄は補う
        for attr in ("filed", "published", "title", "status", "doc_no"):
            if not getattr(cur, attr) and getattr(r, attr):
                setattr(cur, attr, getattr(r, attr))
        if len(r.applicants) > len(cur.applicants):
            cur.applicants = r.applicants
        if len(r.fi_codes) > len(cur.fi_codes):
            cur.fi_codes = r.fi_codes
    return list(best.values()), len(records) - len(best)


# ---------------------------------------------------------------- 名寄せ

class NameMap:
    def __init__(self, path: Optional[Path]):
        self.entries: List[Tuple[str, str, str]] = []  # (正規化した表記, 統合先, 区分)
        if path and path.exists():
            for row in sniff_rows(path):
                label = row.get("表記", "")
                target = row.get("統合先", "")
                kind = row.get("区分", "")
                if not label or label.startswith("{"):
                    continue  # テンプレートの見本行は読まない
                self.entries.append((norm_company(label), norm_text(target), norm_text(kind)))
        # 長い表記から先に当てる（「{会社名}」より「{会社名}（海外子会社）」を先に）
        self.entries.sort(key=lambda e: -len(e[0]))

    def resolve(self, name: str) -> Tuple[str, str, bool]:
        """(統合先, 区分, 名寄せ表に載っていたか)"""
        n = norm_company(name)
        for label, target, kind in self.entries:
            if n == label:
                return target or name, kind, True
        for label, target, kind in self.entries:
            if label and label in n:
                return target or name, kind, True
        return norm_text(name), "", False


# ---------------------------------------------------------------- 集計

def fi_key(code: str, level: str) -> str:
    if level == "subclass":
        return code[:4]
    if level == "maingroup":
        return code.split("/")[0]
    return code


def year_of(r: Record, date_kind: str) -> Optional[int]:
    d = r.filed if date_kind == "出願日" else r.published
    return d.year if d else None


def pct(n: int, total: int) -> float:
    return (100.0 * n / total) if total else 0.0


def md_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------- SVG

FONT = "Hiragino Sans, 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif"
INK = "#1f2937"
MUTED = "#6b7280"
BAR = "#2563eb"
BAR_PARTIAL = "#93c5fd"
BAR_B = "#f59e0b"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def nice_step(x: float) -> int:
    """目盛りの間隔を 1・2・5×10^n にそろえる。"""
    x = max(x, 1)
    mag = 10 ** (len(str(int(x))) - 1)
    for m in (1, 2, 5, 10):
        if x <= m * mag:
            return int(m * mag)
    return int(10 * mag)


def svg_yearly(counts: Dict[int, int], partial_from: int, title: str, note: str) -> str:
    years = sorted(counts)
    if not years:
        return ""
    w, h = 720, 360
    left, right, top, bottom = 56, 16, 48, 72
    pw, ph = w - left - right, h - top - bottom
    raw_max = max(counts.values()) or 1
    tick = nice_step(raw_max / 4)
    vmax = tick * max(1, -(-raw_max // tick))  # 目盛りのきりの良い上限
    step = pw / len(years)
    bw = step * 0.7
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="{FONT}">',
        f'<text x="{left}" y="24" font-size="15" font-weight="bold" fill="{INK}">{esc(title)}</text>',
    ]
    # 目盛り
    n_ticks = int(round(vmax / tick))
    for i in range(n_ticks + 1):
        v = tick * i
        y = top + ph - ph * v / vmax
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w - right}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="{MUTED}">{int(round(v))}</text>')
    for i, yv in enumerate(years):
        v = counts[yv]
        bh = ph * v / vmax
        x = left + step * i + (step - bw) / 2
        y = top + ph - bh
        color = BAR_PARTIAL if yv >= partial_from else BAR
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}"/>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" font-size="11" text-anchor="middle" fill="{INK}">{v}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{top + ph + 16:.1f}" font-size="11" text-anchor="middle" fill="{MUTED}">{yv}</text>')
    parts.append(f'<text x="{left}" y="{h - 30}" font-size="11" fill="{MUTED}">{esc(note)}</text>')
    parts.append(f'<rect x="{left}" y="{h - 20}" width="10" height="10" fill="{BAR_PARTIAL}"/>'
                 f'<text x="{left + 14}" y="{h - 11}" font-size="11" fill="{MUTED}">薄い色＝未公開の出願が残る年（参考値）</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def svg_fi_shift(rows: List[Tuple[str, float, float]], label_a: str, label_b: str, title: str, note: str) -> str:
    if not rows:
        return ""
    w = 720
    left, right, top = 120, 130, 56  # 右に「26.3%→32.3%」の文字が入る余白
    rh = 26
    h = top + rh * len(rows) + 56
    pw = w - left - right
    vmax = max(max(a, b) for _, a, b in rows) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="{FONT}">',
        f'<text x="16" y="24" font-size="15" font-weight="bold" fill="{INK}">{esc(title)}</text>',
        f'<rect x="{left}" y="34" width="10" height="10" fill="{BAR}"/><text x="{left + 14}" y="43" font-size="11" fill="{MUTED}">{esc(label_a)}</text>',
        f'<rect x="{left + 150}" y="34" width="10" height="10" fill="{BAR_B}"/><text x="{left + 164}" y="43" font-size="11" fill="{MUTED}">{esc(label_b)}</text>',
    ]
    for i, (code, a, b) in enumerate(rows):
        y = top + rh * i
        parts.append(f'<text x="{left - 8}" y="{y + 15}" font-size="12" text-anchor="end" fill="{INK}">{esc(code)}</text>')
        wa = pw * a / vmax
        wb = pw * b / vmax
        parts.append(f'<rect x="{left}" y="{y + 2}" width="{wa:.1f}" height="10" fill="{BAR}"/>')
        parts.append(f'<rect x="{left}" y="{y + 13}" width="{wb:.1f}" height="10" fill="{BAR_B}"/>')
        parts.append(f'<text x="{left + max(wa, wb) + 6:.1f}" y="{y + 16}" font-size="11" fill="{MUTED}">{a:.1f}%→{b:.1f}%</text>')
    parts.append(f'<text x="16" y="{h - 16}" font-size="11" fill="{MUTED}">{esc(note)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------- 本体

def main() -> int:
    ap = argparse.ArgumentParser(description="J-PlatPat母集団の集計と図（標準ライブラリのみ）")
    ap.add_argument("--input", nargs="+", required=True, help="results.tsv や J-PlatPat の CSV（複数可）")
    ap.add_argument("--name-map", help="名寄せ表 name_map.csv（表記,統合先,区分,根拠,メモ）")
    ap.add_argument("--applicants-csv", help="出願人が全員入ったCSV（J-PlatPat の国内文献CSV、特許庁APIの書誌を表にしたものなど）")
    ap.add_argument("--company", default="", help="対象の会社名（名寄せ表の統合先）。共同出願人から外す")
    ap.add_argument("--out", required=True, help="出力フォルダ（charts/）")
    ap.add_argument("--date", choices=["出願日", "公知日"], default="出願日", help="年を切る日付の種類")
    ap.add_argument("--fi-level", choices=["subclass", "maingroup", "full"], default="maingroup")
    ap.add_argument("--fi-mode", choices=["first", "all"], default="first",
                    help="first＝筆頭FIだけ数える（既定・重複なし）／all＝付いているFIを全部数える")
    ap.add_argument("--split-year", type=int, help="前半・後半を分ける年（この年から後半）。既定は期間の中間")
    ap.add_argument("--top", type=int, default=12, help="FIの上位いくつを出すか")
    ap.add_argument("--as-of", default=dt.date.today().isoformat(), help="取得日（YYYY-MM-DD）。未公開分の印に使う")
    ap.add_argument("--col", action="append", default=[], help="列名の指定 例：--col 出願日=出願年月日")
    args = ap.parse_args()

    overrides: Dict[str, str] = {}
    for item in args.col:
        if "=" not in item:
            raise SystemExit(f"--col は キー=列名 の形で：{item}")
        k, v = item.split("=", 1)
        overrides[norm_text(k)] = v

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    as_of = parse_date(args.as_of) or dt.date.today()
    # 出願から公開まで約1年半。取得日の前年以降の出願は未公開分が残る（参考値）
    partial_from = as_of.year - 1

    records, notes = load_records([Path(p) for p in args.input], overrides)
    records, dup = dedupe(records)
    names = NameMap(Path(args.name_map) if args.name_map else None)

    total = len(records)
    md: List[str] = []
    md.append(f"# 集計結果（{args.company or '対象'}）\n")
    md.append(f"- 入力：{', '.join(Path(p).name for p in args.input)}")
    md.append(f"- 件数：**{total} 件**（出願単位。出願番号の重複 {dup} 件を除いた）")
    md.append(f"- 年の切り方：{args.date}。取得日 {as_of.isoformat()}。**{partial_from} 年以降は未公開の出願が残る参考値**")
    md.append(f"- FI：{args.fi_level}・{'筆頭FIのみ' if args.fi_mode == 'first' else '付与されたFIを全部'}で数えた")
    for n in notes:
        md.append(f"- ⚠ {n}")
    md.append("")

    # --- 年次
    yearly = Counter()
    no_date = 0
    for r in records:
        y = year_of(r, args.date)
        if y is None:
            no_date += 1
        else:
            yearly[y] += 1
    if yearly:
        years = sorted(yearly)
        md.append("## 1. 年ごとの件数\n")
        md.append(md_table(["年", "件数", "印"], [(y, yearly[y], "未公開分あり" if y >= partial_from else "") for y in years]))
        if no_date:
            md.append(f"\n日付が読めなかった行：{no_date} 件（集計から外した）")
        md.append("")
        with open(out / "yearly.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["年", "件数", "未公開分あり"])
            for y in years:
                w.writerow([y, yearly[y], "1" if y >= partial_from else ""])
        svg = svg_yearly(dict(yearly), partial_from, f"{args.company or '対象'}：出願年ごとの件数",
                         f"出願単位・{args.date}で集計。J-PlatPat（{as_of.isoformat()}取得）")
        (out / "yearly.svg").write_text(svg, encoding="utf-8")

    # --- FI
    has_fi = any(r.fi_codes for r in records)
    if has_fi and yearly:
        years = sorted(yearly)
        split = args.split_year or years[0] + (years[-1] - years[0] + 1) // 2
        fi_all = Counter()
        fi_a, fi_b = Counter(), Counter()
        first_seen: Dict[str, int] = {}
        n_a = n_b = 0
        for r in records:
            if not r.fi_codes:
                continue
            y = year_of(r, args.date)
            codes = [r.fi_codes[0]] if args.fi_mode == "first" else r.fi_codes
            keys = sorted(set(fi_key(c, args.fi_level) for c in codes))
            if y is not None:
                if y < split:
                    n_a += 1
                else:
                    n_b += 1
            for k in keys:
                fi_all[k] += 1
                if y is not None:
                    (fi_a if y < split else fi_b)[k] += 1
                    first_seen[k] = min(first_seen.get(k, 9999), y)
        top = [k for k, _ in fi_all.most_common(args.top)]
        label_a = f"前半 {years[0]}–{split - 1}（{n_a}件）"
        label_b = f"後半 {split}–{years[-1]}（{n_b}件）"
        rows = [(k, fi_all[k], pct(fi_a[k], n_a), pct(fi_b[k], n_b)) for k in top]
        md.append(f"## 2. FIの上位{len(top)}と前半・後半のシェア\n")
        md.append(md_table(["FI", "件数", label_a, label_b, "差（ポイント）"],
                           [(k, c, f"{a:.1f}%", f"{b:.1f}%", f"{b - a:+.1f}") for k, c, a, b in rows]))
        md.append("\nシェア＝その期間の件数に占める割合。後半は未公開分が残るので、件数より割合で見る。\n")
        rising = sorted(((k, pct(fi_b[k], n_b) - pct(fi_a[k], n_a), fi_b[k]) for k in fi_all if fi_b[k] >= 3),
                        key=lambda t: -t[1])[:8]
        if rising:
            md.append("### 後半に割合が伸びた分類（後半3件以上）\n")
            md.append(md_table(["FI", "差（ポイント）", "後半の件数"], [(k, f"{d:+.1f}", n) for k, d, n in rising]))
            md.append("")
        newcomers = sorted(((k, first_seen[k], fi_all[k]) for k in first_seen if first_seen[k] >= split and fi_all[k] >= 2),
                           key=lambda t: (-t[2], t[1]))[:10]
        if newcomers:
            md.append("### 後半に初めて現れた分類（2件以上）\n")
            md.append(md_table(["FI", "初出の年", "件数"], newcomers))
            md.append("\n※ 母集団の中で初めて現れたという意味。会社として初めてとは限らない（期間より前は見ていない）。\n")
        with open(out / "fi_share.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["FI", "件数", "前半シェア%", "後半シェア%", "差pt", "初出の年"])
            for k in sorted(fi_all, key=lambda x: -fi_all[x]):
                w.writerow([k, fi_all[k], f"{pct(fi_a[k], n_a):.2f}", f"{pct(fi_b[k], n_b):.2f}",
                            f"{pct(fi_b[k], n_b) - pct(fi_a[k], n_a):.2f}", first_seen.get(k, "")])
        svg = svg_fi_shift([(k, a, b) for k, _, a, b in rows[:10]], label_a, label_b,
                           f"{args.company or '対象'}：FI（{args.fi_level}）のシェアの変化",
                           f"{'筆頭FI' if args.fi_mode == 'first' else '全FI'}・出願単位・{args.date}。J-PlatPat（{as_of.isoformat()}取得）")
        (out / "fi_share.svg").write_text(svg, encoding="utf-8")
    elif not has_fi:
        md.append("## 2. FI\n\nFIの列が無いため集計していない（J-PlatPat の CSV 出力か、一覧の FI 列つき抽出を使う）。\n")

    # --- 出願人（筆頭・名寄せ後）
    applicant_counts = Counter()
    unmatched = Counter()
    for r in records:
        if not r.applicants:
            continue
        target, kind, hit = names.resolve(r.applicants[0])
        applicant_counts[target] += 1
        if not hit:
            unmatched[norm_text(r.applicants[0])] += 1
    if applicant_counts:
        md.append("## 3. 出願人（筆頭・名寄せ後）\n")
        md.append(md_table(["出願人（統合先）", "件数", "割合"],
                           [(k, v, f"{pct(v, total):.1f}%") for k, v in applicant_counts.most_common(15)]))
        md.append("")
        if names.entries and unmatched:
            md.append("### 名寄せ表に無かった表記（上位15。名寄せ表を足す材料）\n")
            md.append(md_table(["表記", "件数"], unmatched.most_common(15)))
            md.append("")
        with open(out / "applicants.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["統合先", "件数"])
            for k, v in applicant_counts.most_common():
                w.writerow([k, v])

    # --- 共同出願人（出願人が全員入ったCSVがあるとき）
    if args.applicants_csv:
        co_rows = sniff_rows(Path(args.applicants_csv))
        if co_rows:
            hdr = co_rows[0].keys()
            c_no = find_column(hdr, "出願番号", overrides)
            c_ap = find_column(hdr, "出願人", overrides)
            c_dt = find_column(hdr, "出願日", overrides)
            if not (c_no and c_ap):
                md.append("## 4. 共同出願人\n\n⚠ 出願人CSVに出願番号・出願人の列が見つからなかった。\n")
            else:
                own_kinds = {"本体", "子会社", "旧社名"}
                co = Counter()
                co_kind = {}
                by_period = defaultdict(Counter)
                n_joint = 0
                n_base = 0
                for row in co_rows:
                    names_all = split_applicants(row.get(c_ap, ""))
                    if not names_all:
                        continue
                    n_base += 1
                    d = parse_date(row.get(c_dt, "")) if c_dt else None
                    others = []
                    for nm in names_all:
                        target, kind, hit = names.resolve(nm)
                        is_own = (hit and (kind in own_kinds or (args.company and target == args.company)))
                        if not is_own:
                            others.append(target)
                    if others:
                        n_joint += 1
                    for o in sorted(set(others)):
                        co[o] += 1
                        co_kind[o] = "大学・研究機関" if ACADEMIC_WORDS.search(o) else "企業・その他"
                        if d:
                            by_period[o][f"{d.year // 3 * 3}–{d.year // 3 * 3 + 2}"] += 1
                md.append("## 4. 共同出願人（出願人が全員入ったデータから）\n")
                md.append(f"- 共同出願の件数：{n_joint} / {n_base} 件（{pct(n_joint, n_base):.1f}%）")
                md.append("- 種類は名前からの目安（大学・研究所などの語で判定）。顧客か合弁かは人が確かめる\n")
                md.append(md_table(["相手", "種類", "件数", "3年ごと"],
                                   [(k, co_kind[k], v, " ".join(f"{p}:{c}" for p, c in sorted(by_period[k].items())))
                                    for k, v in co.most_common(20)]))
                md.append("")
                with open(out / "coapplicants.csv", "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["相手", "種類", "件数"])
                    for k, v in co.most_common():
                        w.writerow([k, co_kind[k], v])

    md.append("## 注意（結果を文書に書くとき）\n")
    md.append("- 件数は出願単位。件数の多さは技術の強さを示さない")
    md.append(f"- {partial_from} 年以降は未公開の出願が残る。伸び・減りの判断に使わない")
    md.append("- 名寄せ表に無い子会社は別の出願人として数えている（上の「名寄せ表に無かった表記」を確認）")
    (out / "aggregate.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"書き出し：{out / 'aggregate.md'}（{total} 件）")
    for n in notes:
        print(f"⚠ {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
