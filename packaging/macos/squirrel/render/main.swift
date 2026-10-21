// Shows a SquirrelPanel with the theme of a Rime user directory and saves a picture of it,
// so the skin patch can be checked without typing into a real application.
//
//   render <user dir> <shared data dir> <output.png> [candidate count]
//
// Compiled together with Squirrel's sources, replacing Main.swift.

import AppKit

enum SquirrelApp {
  static let userDir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
  static let appDir = URL(fileURLWithPath: "/Library/Input Methods/Squirrel.app")
  static let logDir = userDir.appending(component: "log", directoryHint: .isDirectory)
}

// A test skin whose regions are easy to tell apart: four colored corners of different sizes,
// gray edges and a striped center, drawn at 2x so the scale handling is exercised too.
func writeTestSkin(to url: URL) throws {
  let size = NSSize(width: 200, height: 100), slice = NSEdgeInsets(top: 20, left: 40, bottom: 30, right: 60)
  let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 400, pixelsHigh: 200, bitsPerSample: 8, samplesPerPixel: 4,
                             hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
  rep.size = size
  NSGraphicsContext.saveGraphicsState()
  NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
  NSColor(white: 0.6, alpha: 1).setFill()
  NSRect(origin: .zero, size: size).fill()
  NSColor.white.setFill()
  let center = NSRect(x: slice.left, y: slice.bottom, width: size.width - slice.left - slice.right,
                      height: size.height - slice.top - slice.bottom)
  center.fill()
  NSColor(red: 1, green: 0.8, blue: 0.9, alpha: 1).setFill()
  for x in stride(from: center.minX, to: center.maxX, by: 10) {
    NSRect(x: x, y: center.minY, width: 5, height: center.height).fill()
  }
  let corners: [(NSColor, NSRect)] = [
    (.systemRed, NSRect(x: 0, y: size.height - slice.top, width: slice.left, height: slice.top)),
    (.systemGreen, NSRect(x: size.width - slice.right, y: size.height - slice.top, width: slice.right, height: slice.top)),
    (.systemBlue, NSRect(x: 0, y: 0, width: slice.left, height: slice.bottom)),
    (.systemYellow, NSRect(x: size.width - slice.right, y: 0, width: slice.right, height: slice.bottom)),
  ]
  for (color, rect) in corners {
    color.setFill()
    rect.fill()
  }
  NSGraphicsContext.restoreGraphicsState()
  try rep.representation(using: .png, properties: [:])!.write(to: url)
}

func writePNG(_ rep: NSBitmapImageRep, _ path: String) {
  try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
}

let args = CommandLine.arguments
let rimeAPI = rime_get_api_stdbool().pointee
try writeTestSkin(to: SquirrelApp.userDir.appending(component: "skin.png"))
try FileManager.default.createDirectory(at: SquirrelApp.logDir, withIntermediateDirectories: true)

var traits = RimeTraits.rimeStructInit()
traits.setCString(args[2], to: \.shared_data_dir)
traits.setCString(SquirrelApp.userDir.path(), to: \.user_data_dir)
traits.setCString(SquirrelApp.logDir.path(), to: \.log_dir)
traits.setCString("rime.render", to: \.app_name)
rimeAPI.setup(&traits)
rimeAPI.initialize(nil)
guard rimeAPI.deploy_config_file("squirrel.yaml", "config_version") else { fatalError("cannot deploy squirrel.yaml") }
let config = SquirrelConfig()
guard config.openBaseConfig() else { fatalError("cannot open squirrel config") }

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.finishLaunching()
let screen = NSScreen.main!.frame
let panel = SquirrelPanel(position: NSRect(x: screen.minX + 300, y: screen.maxY - 300, width: 0, height: 20))
panel.load(config: config, forDarkMode: false)
panel.load(config: config, forDarkMode: true)
let words = ["测试", "测速", "厕所", "策士", "侧视", "侧室", "册"]
let count = args.count > 4 ? Int(args[4])! : words.count
let candidates = Array(words.prefix(count))
panel.update(preedit: "ceshi", selRange: NSRange(location: 0, length: 5), caretPos: 5, candidates: candidates,
             comments: candidates.map { _ in "" }, labels: [], highlighted: 0, page: 0, lastPage: false, update: true)
RunLoop.main.run(until: Date(timeIntervalSinceNow: 1.5))

let content = panel.contentView!
print("window \(panel.frame) visible \(panel.isVisible) scale \(panel.backingScaleFactor)")
for view in content.subviews {
  print("  \(type(of: view)) frame \(view.frame) bounds \(view.bounds) hidden \(view.isHidden)")
}

// The window server's picture is what a user sees; the view's own rendering is a fallback when
// the runner does not allow screen capture.
let capture = Process()
capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
capture.arguments = ["-x", "-o", "-l\(panel.windowNumber)", args[3]]
try capture.run()
capture.waitUntilExit()
print("screencapture status \(capture.terminationStatus)")
if let rep = content.bitmapImageRepForCachingDisplay(in: content.bounds) {
  content.cacheDisplay(in: content.bounds, to: rep)
  writePNG(rep, args[3].replacingOccurrences(of: ".png", with: "-view.png"))
}
