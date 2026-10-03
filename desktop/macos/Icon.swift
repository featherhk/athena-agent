import AppKit
let destination = CommandLine.arguments[1]
let mobile = CommandLine.arguments.dropFirst(2).contains("--ios")
let sizes = mobile ? [1024] : [16, 32, 128, 256, 512]
try FileManager.default.createDirectory(atPath: destination, withIntermediateDirectories: true)
for size in sizes {
    for scale in (mobile ? [1] : [1, 2]) {
        let px = size * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                                  bitsPerSample: 8, samplesPerPixel: mobile ? 3 : 4, hasAlpha: !mobile,
                                  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        let t = AffineTransform(scale: CGFloat(px) / 1024)
        (t as NSAffineTransform).concat()
        NSColor(calibratedRed: 0.969, green: 0.955, blue: 0.925, alpha: 1).setFill()
        if mobile { NSBezierPath(rect: NSRect(x: 0, y: 0, width: 1024, height: 1024)).fill() }
        else { NSBezierPath(roundedRect: NSRect(x: 56, y: 56, width: 912, height: 912), xRadius: 204, yRadius: 204).fill() }
        let transform = NSAffineTransform()
        transform.translateX(by: 128, yBy: 890)
        transform.scaleX(by: 8, yBy: -8)
        transform.concat()
        NSColor(calibratedRed: 0.31, green: 0.27, blue: 0.37, alpha: 1).setStroke()
        func path(_ draw: (NSBezierPath) -> Void) {
            let p = NSBezierPath(); p.lineWidth = 2.0; p.lineCapStyle = .round; p.lineJoinStyle = .round; draw(p); p.stroke()
        }
        func pt(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: x, y: y) }
        path { p in
            p.move(to: pt(24,29)); p.line(to: pt(34,32)); p.curve(to: pt(62,32), controlPoint1: pt(42,29), controlPoint2: pt(54,29)); p.line(to: pt(72,29)); p.line(to: pt(68,44)); p.curve(to: pt(48,77), controlPoint1: pt(72,60), controlPoint2: pt(63,76)); p.curve(to: pt(28,45), controlPoint1: pt(33,76), controlPoint2: pt(24,62)); p.close()
        }
        path { p in
            p.move(to: pt(26,40)); p.curve(to: pt(45,40), controlPoint1: pt(31,32), controlPoint2: pt(40,33)); p.curve(to: pt(26,40), controlPoint1: pt(39,49), controlPoint2: pt(30,48))
            p.move(to: pt(51,40)); p.curve(to: pt(70,40), controlPoint1: pt(57,32), controlPoint2: pt(66,33)); p.curve(to: pt(51,40), controlPoint1: pt(66,48), controlPoint2: pt(57,49))
            p.move(to: pt(44,47)); p.line(to: pt(48,54)); p.line(to: pt(52,47))
            p.move(to: pt(31,50)); p.curve(to: pt(41,71), controlPoint1: pt(30,61), controlPoint2: pt(35,68))
            p.move(to: pt(62,51)); p.curve(to: pt(54,72), controlPoint1: pt(66,60), controlPoint2: pt(61,69))
        }
        NSColor(calibratedRed: 0.31, green: 0.27, blue: 0.37, alpha: 1).setFill()
        NSBezierPath(ovalIn: NSRect(x: 33.1, y: 37.6, width: 4.8, height: 4.8)).fill()
        NSBezierPath(ovalIn: NSRect(x: 58.6, y: 37.6, width: 4.8, height: 4.8)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination).appendingPathComponent(name))
    }
}
