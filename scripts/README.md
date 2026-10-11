# scripts

手順（`../method/`）で使う小さなスクリプトです。作業は `./work/{会社名}/` のような作業フォルダの中で行い、出力はそちらに置きます（`work/` は `.gitignore` で除外してあります）。

| ファイル | 1行の説明 | 手順 |
|---|---|---|
| `ipl_aggregate.py` | J-PlatPat の一覧（TSV／CSV）を名寄せ表でまとめ、年・FI・出願人・共同出願人の集計と SVG の図を出す（標準ライブラリのみ・Python 3.9 以上） | `03` |
| `edinet_fetch.py` | EDINET API で有価証券報告書を探し（`find`）、PDF／CSV を保存し（`get`）、セグメント・研究開発費などの行を拾う（`extract`） | `04` |
| `biblio_search.py` | 国立国会図書館サーチと J-STAGE から、会社に関する書籍・雑誌・論文の候補リストを Markdown で作る | `04` |
| `render_report.py` | Markdown の分析メモを A4 の HTML にし、Google Chrome のヘッドレス印刷で PDF にする（`uv run` で起動。`markdown` パッケージは uv が用意する） | 出力 |
| `pdf_text.swift` | PDF の文字を取り出す（macOS 標準の PDFKit。`swift pdf_text.swift 入力.pdf > 出力.txt`） | `04` |

## API キーの置き場

EDINET の API キー（無料。https://api.edinet-fsa.go.jp/WEEE0090.aspx で本人が登録）だけが必要です。

- 環境変数 `EDINET_API_KEY` に入れる、または
- 作業するフォルダ（カレント）の `./.env` に `EDINET_API_KEY=…` と1行書く

**`.env` と鍵は、git にコミットしません**（`.gitignore` 済み）。画面・ログ・文書にキーを出さないでください。

他のスクリプト（J-PlatPat の集計、NDLサーチ、J-STAGE）にキーは要りません。

## 動作の前提

- Python 3.9 以上
- `render_report.py`：`uv` と Google Chrome
- `pdf_text.swift`：macOS と Swift
- J-PlatPat の CSV は、利用者本人がログインして画面から出力したものを使います。このリポジトリのスクリプトは、J-PlatPat に自動でアクセスしません
