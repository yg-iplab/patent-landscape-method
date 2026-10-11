// PDF の文字を取り出す（macOS 標準の PDFKit。追加インストール不要）
// 使い方: swift pdf_text.swift 入力.pdf > 出力.txt
// 統合報告書・有価証券報告書・公的資料のPDFを読むときに使う。
// 注意: 文字が画像になっているPDF（一部の統合報告書）は取り出せない → 画面で読むか、頁を画像にして読む
import Foundation
import PDFKit

let args = CommandLine.arguments
guard args.count >= 2, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
  FileHandle.standardError.write("PDFを開けませんでした\n".data(using: .utf8)!)
  exit(1)
}
var out = ""
for i in 0..<doc.pageCount {
  out += "\n=== PAGE \(i + 1) ===\n"
  out += doc.page(at: i)?.string ?? ""
}
print(out)
