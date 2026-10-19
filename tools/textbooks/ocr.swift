// Распознать скан учебника Apple Vision (только macOS): строки с местом на странице.
//   swiftc -O ocr.swift -o ocr && ./ocr учебник.pdf <id>.lines.json
// Выход: [[["текст", x0, y0, x1, y1], …], …] по страницам, доли 0…1, y сверху вниз.
// Текст страниц для индекса: python -m app.tools.index_textbook учебник.pdf <id> --lines <id>.lines.json
import Foundation
import PDFKit
import Vision

let args = CommandLine.arguments
guard args.count >= 3, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("usage: ocr in.pdf out.json [from] [to]\n".data(using: .utf8)!)
    exit(1)
}
let from = args.count > 3 ? Int(args[3])! : 0
let to = args.count > 4 ? min(Int(args[4])!, doc.pageCount) : doc.pageCount
var pages: [[[Any]]] = []

for i in from..<to {
    autoreleasepool {
        guard let page = doc.page(at: i) else { pages.append([]); return }
        let box = page.bounds(for: .mediaBox)
        let scale = 2200.0 / max(box.width, box.height)
        let image = page.thumbnail(of: CGSize(width: box.width * scale, height: box.height * scale), for: .mediaBox)
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { pages.append([]); return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["ru-RU", "en-US"]
        request.usesLanguageCorrection = true
        try? VNImageRequestHandler(cgImage: cg).perform([request])
        let lines = (request.results ?? [])
            .compactMap { obs -> (CGRect, String)? in
                guard let t = obs.topCandidates(1).first?.string else { return nil }
                return (obs.boundingBox, t)
            }
        // Две колонки: сначала левая, потом правая; внутри — сверху вниз.
        let sorted = lines.sorted { a, b in
            let colA = a.0.maxX < 0.5 ? 0 : (a.0.minX > 0.5 ? 1 : 0)
            let colB = b.0.maxX < 0.5 ? 0 : (b.0.minX > 0.5 ? 1 : 0)
            if colA != colB { return colA < colB }
            return a.0.midY > b.0.midY
        }
        pages.append(sorted.map { r in [r.1, Double(r.0.minX), Double(1 - r.0.maxY), Double(r.0.maxX), Double(1 - r.0.minY)] as [Any] })
    }
    if i % 20 == 0 { FileHandle.standardError.write("\(i)/\(to)\n".data(using: .utf8)!) }
}
let data = try JSONSerialization.data(withJSONObject: pages)
try data.write(to: URL(fileURLWithPath: args[2]))
