// Страницы PDF в JPEG для вырезок: ./render учебник.pdf <outdir> [1600]
// Потом в серые: картинки учебника лежат в data/textbooks/<id>/0000.jpg.
import AppKit
import Foundation
import PDFKit

let args = CommandLine.arguments
guard args.count >= 3, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else { exit(1) }
let out = URL(fileURLWithPath: args[2])
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let side = args.count > 3 ? Double(args[3])! : 1600

for i in 0..<doc.pageCount {
    autoreleasepool {
        let file = out.appendingPathComponent(String(format: "%04d.jpg", i))
        if FileManager.default.fileExists(atPath: file.path) { return }
        guard let page = doc.page(at: i) else { return }
        let box = page.bounds(for: .mediaBox)
        let scale = side / max(box.width, box.height)
        let image = page.thumbnail(of: CGSize(width: box.width * scale, height: box.height * scale), for: .mediaBox)
        guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
              let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.72]) else { return }
        try? jpeg.write(to: file)
    }
}
print("\(args[2]): \(doc.pageCount)")
