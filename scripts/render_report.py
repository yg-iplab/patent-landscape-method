# /// script
# requires-python = ">=3.9"
# dependencies = ["markdown>=3.5"]
# ///
# -*- coding: utf-8 -*-
"""
分析メモ（Markdown）→ HTML（A4・印刷用）→ PDF。

  uv run render_report.py ./work/{会社名}/report_v1.md --pdf
  uv run render_report.py report_v1.md --pdf --preview   # 1ページ目のPNGも出す（確認用）

  - 先頭の YAML（--- で囲んだ部分）は表示しない。pages: 4 があればページ数を確かめる
  - <!-- pagebreak --> で改ページ
  - 画像が .svg なら、ファイルの中身をそのまま埋め込む（PDFでも線がきれい）
  - PDF は Google Chrome のヘッドレス印刷で作る（追加インストール不要）
  - 出力は md と同じフォルダの out/（git には入れない）
  - markdown パッケージは uv が一時的に用意する（`uv run` で起動すること）
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 14mm 15mm 14mm 15mm; }
:root { --ink:#111827; --muted:#6b7280; --line:#d1d5db; --accent:#1d4ed8; --soft:#eff6ff; }
html { font-family: "Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif; color: var(--ink); }
body { font-size: 10pt; line-height: 1.6; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 2mm; border-bottom: 2px solid var(--accent); padding-bottom: 1.5mm; }
h2 { font-size: 12.5pt; margin: 5mm 0 2mm; color: var(--accent); }
h3 { font-size: 11pt; margin: 4mm 0 1.5mm; }
p { margin: 1.5mm 0; }
ul, ol { margin: 1mm 0 1mm 5mm; padding-left: 3mm; }
li { margin: 0.6mm 0; }
table { border-collapse: collapse; width: 100%; margin: 2mm 0; font-size: 9pt; }
th, td { border: 1px solid var(--line); padding: 1.2mm 1.8mm; vertical-align: top; }
th { background: #f3f4f6; text-align: left; }
img, svg { max-width: 100%; height: auto; display: block; margin: 2mm auto; }
code { font-family: Menlo, monospace; font-size: 8.5pt; background: #f3f4f6; padding: 0 1mm; }
.meta { color: var(--muted); font-size: 9pt; }
.note { color: var(--muted); font-size: 8.5pt; margin-top: 0; }
.limit { font-size: 8.5pt; background: var(--soft); border-left: 3px solid var(--accent); padding: 1.5mm 2.5mm; }
.pb { break-after: page; page-break-after: always; height: 0; }
strong { color: #0f172a; }
"""


def strip_front_matter(text: str):
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        text = text[m.end():]
    return meta, text


def inline_svgs(html: str, base: Path) -> str:
    def repl(m):
        src = m.group(2)
        if src.lower().endswith(".svg"):
            p = (base / src).resolve()
            if p.exists():
                svg = p.read_text(encoding="utf-8")
                svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
                return svg
            return f'<p class="note">⚠ 図が見つかりません：{src}</p>'
        return m.group(0)
    return re.sub(r'<img\s+[^>]*?(alt="[^"]*"\s+)?src="([^"]+)"[^>]*>', repl, html)


def count_pdf_pages(pdf: Path) -> int:
    data = pdf.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data))


def main() -> int:
    ap = argparse.ArgumentParser(description="分析メモ：md → HTML → PDF")
    ap.add_argument("md", help="report_vN.md など（Markdown）")
    ap.add_argument("--pdf", action="store_true", help="PDFも作る（Chrome）")
    ap.add_argument("--preview", action="store_true", help="1ページ目のPNGも作る（確認用）")
    ap.add_argument("--out", help="出力フォルダ（既定：md と同じ場所の out/）")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    meta, body = strip_front_matter(md_path.read_text(encoding="utf-8"))
    body = body.replace("<!-- pagebreak -->", '\n<div class="pb"></div>\n')
    html_body = markdown.markdown(body, extensions=["tables", "sane_lists", "attr_list", "md_in_html"])
    html_body = inline_svgs(html_body, md_path.parent)
    title = meta.get("company", md_path.stem)
    html = (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{html_body}</body></html>')

    out = Path(args.out).resolve() if args.out else md_path.parent / "out"
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / (md_path.stem + ".html")
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML：{html_path}")

    if args.pdf or args.preview:
        if not Path(CHROME).exists():
            print("⚠ Google Chrome が見つからないため PDF を作れません。HTML を Chrome で開いて印刷してください。")
            return 1
    if args.pdf:
        pdf_path = out / (md_path.stem + ".pdf")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pages = count_pdf_pages(pdf_path)
        print(f"PDF：{pdf_path}（{pages} ページ）")
        limit = meta.get("pages")
        if limit and limit.isdigit() and pages > int(limit):
            print(f"⚠ {pages} ページ。上限 {limit} ページを超えています。削ってください")
    if args.preview:
        png = out / (md_path.stem + "_p1.png")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--window-size=794,1123", f"--screenshot={png}", html_path.as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"確認用PNG：{png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
