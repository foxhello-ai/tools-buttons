import AppKit
import WebKit

final class TaskWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKDownloadDelegate {
    let window: NSWindow
    let webView: WKWebView
    var wantsVisible = false
    private var origin: URL?
    private var currentURL: URL?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 880, height: 650),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "项目任务"
        window.minSize = NSSize(width: 500, height: 400)
        window.isReleasedWhenClosed = false
        window.contentView = webView
        window.delegate = self
        webView.navigationDelegate = self
        window.center()
    }

    func open(baseURL: URL, runID: String? = nil, projectName: String) {
        origin = baseURL
        var parts = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        parts.path = "/"
        parts.queryItems = [URLQueryItem(name: "desktop", value: "1")]
        parts.fragment = runID.map { "run=\($0)" } ?? "history"
        let url = parts.url!
        // Reload to obtain a fresh interface token after a backend restart.
        if currentURL != url || !window.isVisible { webView.load(URLRequest(url: url)) }
        currentURL = url
        window.title = "\(projectName) · 项目任务"
        wantsVisible = true
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applyVisibility(_ visible: Bool) {
        if !visible { window.orderOut(nil) }
        else if wantsVisible && !window.isVisible { window.orderFront(nil) }
    }

    func windowWillClose(_ notification: Notification) { wantsVisible = false }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let origin else { decisionHandler(.cancel); return }
        let local = url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port
        let localBlob = url.absoluteString.hasPrefix("blob:\(origin.absoluteString)/")
        guard local || localBlob else { decisionHandler(.cancel); return }
        decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let picker = NSSavePanel()
        picker.nameFieldStringValue = suggestedFilename
        picker.beginSheetModal(for: window) { result in completionHandler(result == .OK ? picker.url : nil) }
    }
}
