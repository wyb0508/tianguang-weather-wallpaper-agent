// Dedicated WKWebView, offline, with the scene shipped inside the app bundle.
import Cocoa
import WebKit

final class Probe: NSObject, WKNavigationDelegate {
    var view: WKWebView!
    var window: NSWindow!
    var evidence: [[String: Any]] = []
    let release = CommandLine.arguments.dropFirst(3).contains("--release")
    let file = URL(fileURLWithPath: CommandLine.arguments[1])
    let output = URL(fileURLWithPath: CommandLine.arguments[2])

    func finish(_ message: String? = nil) {
        if let message = message { evidence.append(["error": message]) }
        do { try JSONSerialization.data(withJSONObject: evidence, options: [.prettyPrinted]).write(to: output) }
        catch { fputs("\(error)\n", stderr); exit(2) }
        window?.orderOut(nil)
        NSApp.terminate(nil)
    }
    func start() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let rules = #"[{"trigger":{"url-filter":"^https?://"},"action":{"type":"block"}}]"#
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "wallpaper-qa-offline", encodedContentRuleList: rules) { list, error in
            guard let list = list else { self.finish(error?.localizedDescription ?? "content blocker unavailable"); return }
            config.userContentController.add(list)
            let rect = NSRect(x: 16, y: 56, width: 320, height: 180)
            self.window = NSWindow(contentRect: rect, styleMask: [.borderless], backing: .buffered, defer: false)
            self.view = WKWebView(frame: NSRect(origin: .zero, size: rect.size), configuration: config)
            self.window.contentView = self.view
            self.window.ignoresMouseEvents = true
            self.window.alphaValue = 0.03
            self.window.orderFrontRegardless()
            self.view.navigationDelegate = self
            self.view.loadFileURL(self.release ? self.file : URL(string: "#desktop", relativeTo: self.file)!.absoluteURL,
                                  allowingReadAccessTo: self.file.deletingLastPathComponent())
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(error.localizedDescription)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let script = release ? """
        if(!window.motionReady) throw Error('缺少预览初始化');
        const api=await window.motionReady, cfg=JSON.parse(document.querySelector('#scene').textContent).config;
        const selector=document.querySelector('#preset'),states=[];
        for(const name of Object.keys(cfg.profiles)){
          selector.value=name; selector.dispatchEvent(new Event('change')); await api.whenReady?.();
          const all=[...document.querySelectorAll('#stage [data-material]')],visible=all.filter(i=>getComputedStyle(i).display!=='none');
          const want=cfg.materials.filter(m=>m.weather===name).map(m=>m.path);
          if(JSON.stringify(visible.map(i=>i.dataset.material))!==JSON.stringify(want))throw Error(name+' 材质错误');
          if(cfg.material_loading==='on-demand' && all.length!==visible.length)throw Error('残留材质');
          await Promise.all(visible.map(i=>i.decode?.()));api.sample(1.25);
          const c=api.canvas,p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          let drawn=0;for(let i=3;i<p.length;i+=4)if(p[i])drawn++;
          states.push({weather:name,kind:cfg.profiles[name].kind,loaded:all.length,drawn});
        }
        return {states,engine:'WKWebView'};
        """ : """
        if (!window.wall?.preview) return {unsupported:true};
        window.wall.forecast?.(null);
        const list=[...document.querySelectorAll('#preset option')].map(o=>o.value);
        const states=[];
        for(const w of list){
          window.wall.preview(w);
          await Promise.all([...document.querySelectorAll('#stage img[src]')].map(i=>i.decode()));
          await new Promise(r=>setTimeout(r,90));
          const pixels=id=>{const c=document.getElementById(id);if(!c)return null;
            const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
            let n=0;for(let i=3;i<a.length;i+=4)if(a[i])n++;return n};
          states.push({weather:w,far:pixels('far'),near:pixels('near'),
            snow:+getComputedStyle(document.getElementById('snow')).opacity});
        }
        return {title:document.title,hidden:document.hidden,photo:document.querySelector('#base').naturalWidth,states};
        """
        view.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { result in
            switch result {
            case .success(let obj): if let info = obj as? [String: Any] { self.evidence.append(info) }; self.finish()
            case .failure(let e): self.finish(e.localizedDescription)
            }
        }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let probe = Probe()
probe.start()
DispatchQueue.main.asyncAfter(deadline: .now()+35) { probe.finish("WKWebView probe timed out") }
app.run()
