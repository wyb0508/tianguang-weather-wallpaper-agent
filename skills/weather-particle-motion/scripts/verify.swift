// Run with swift verify.swift DEMO NEW_OUTPUT. Uses the system WebKit, offline.
import Cocoa
import WebKit

struct VerificationError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
func check(_ ok: Bool, _ message: String) throws {
    if !ok { throw VerificationError(message: message) }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
@MainActor func verify() async throws {
    try check(CommandLine.arguments.count == 3, "用法：swift verify.swift 演示目录 新输出目录")
    let fm = FileManager.default
    let demo = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true).standardizedFileURL
    let output = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
    try check(!fm.fileExists(atPath: output.path), "验收输出目录已存在")
    try fm.createDirectory(at: output, withIntermediateDirectories: true)
    let probes = try String(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("probes.js"), encoding: .utf8)
    let rect = CGRect(x: 0,y: 0,width: 720,height: 405)
    let win = NSWindow(contentRect: rect, styleMask: [.titled], backing: .buffered, defer: false)
    win.title = "降水动画 · 离线验收"
    let web = WKWebView(frame: rect)
    win.contentView = web
    win.center()
    win.orderFrontRegardless()
    defer { win.orderOut(nil) }
    func wait(_ ms: UInt64) async throws { try await Task.sleep(nanoseconds: ms*1_000_000) }
    func js(_ body: String, _ args: [String:Any] = [:]) async throws -> Any? {
        try await web.callAsyncJavaScript(body, arguments: args, in: nil, contentWorld: .page)
    }
    func load(_ name: String, _ mask: String) async throws {
        let file = demo.appendingPathComponent(name+"/index.html")
        web.loadFileURL(file, allowingReadAccessTo: file.deletingLastPathComponent())
        var ready = false
        for _ in 0..<200 {
            try await wait(50)
            if !web.isLoading, web.url?.path == file.path,
               (try? await js("return document.body.dataset.ready")) as? String == "true" {
                ready = true
                break
            }
        }
        try check(ready, "WKWebView 未就绪：" + name)
        _ = try await web.evaluateJavaScript(probes)
        let maskData = try Data(contentsOf: demo.appendingPathComponent("reference/"+mask+".png"))
        _ = try await js("await prepareProbe(reference)", ["reference":"data:image/png;base64,"+maskData.base64EncodedString()])
    }
    func snapshot(_ name: String) async throws {
        let config = WKSnapshotConfiguration()
        config.afterScreenUpdates = true
        let im = try await web.takeSnapshot(configuration: config)
        guard let tiff = im.tiffRepresentation, let rep = NSBitmapImageRep(data:tiff),
              let png = rep.representation(using:.png, properties:[:]) else {
            throw VerificationError(message:"截图编码失败")
        }
        try png.write(to: output.appendingPathComponent(name+".png"))
    }
    try await load("race","race-car")
    var model = [CChar](repeating: 0, count: 256)
    var modelSize = model.count
    sysctlbyname("hw.model", &model, &modelSize, nil, 0)
    var report: [String:Any] = [
        "hardware_model":String(cString:model),
        "host":"macOS WKWebView, file://", "os":ProcessInfo.processInfo.operatingSystemVersionString,
        "user_agent":try await js("return navigator.userAgent") ?? "",
        "webkit":Bundle(for: WKWebView.self).infoDictionary?["CFBundleVersion"] ?? "",
        "seed":try await js("return weatherMotionScene.config.seed") ?? 0,
        "pixel_ratio":try await js("return devicePixelRatio") ?? 1,
        "measurement":"rAF timestamp at actual module draw callbacks, not display presentation"
    ]
    report["snow"] = try await js("return snowProbe()")
    report["rain_to_clear"] = try await js("return clearProbe()")
    let times = [1.25,1.30,1.35]
    var frames: [[String:Any]] = []
    for (i,t) in times.enumerated() {
        _ = try await js("document.querySelector('.controls').hidden=true; document.querySelector('.controls').style.display='none'; motion.setWeather('大雨'); motion.sample(t)", ["t":t])
        let name = "race-rain-0"+String(i)
        try await snapshot(name)
        frames.append(["file":name+".png","seconds":t,"weather":"大雨"])
    }
    report["frames"] = frames
    report["timing"] = try await js("return await measureFrames()")
    try await load("shore","shore-water")
    let first = try await js("return shoreProbe(1,null)") as? [String:Any]
    guard let hit = first?["ring"] else { throw VerificationError(message:"缺少水纹交点") }
    win.setContentSize(CGSize(width:405,height:640))
    try await wait(150)
    let second = try await js("return shoreProbe(1.2,hit)", ["hit":hit])
    report["shore"] = [first as Any, second as Any]
    _ = try await js("motion.destroy()")
    let json = try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys])
    try json.write(to:output.appendingPathComponent("report.json"))
    print(output.appendingPathComponent("report.json").path)
}
Task { @MainActor in
    do { try await verify(); exit(0) }
    catch { fputs("验收失败：\(error as NSError)\n",stderr); exit(1) }
}
app.run()
