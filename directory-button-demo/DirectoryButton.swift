import AppKit

final class HoverHint {
    let panel: NSPanel

    init(text: String) {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 13)
        label.textColor = .white
        label.alignment = .center
        label.sizeToFit()
        let size = NSSize(width: ceil(label.frame.width) + 20, height: ceil(label.frame.height) + 12)

        panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.ignoresMouseEvents = true

        let content = NSView(frame: NSRect(origin: .zero, size: size))
        content.wantsLayer = true
        content.layer?.cornerRadius = 6
        content.layer?.backgroundColor = NSColor(srgbRed: 0.12, green: 0.14, blue: 0.16, alpha: 0.98).cgColor
        label.frame = NSRect(x: 10, y: 6, width: size.width - 20, height: size.height - 12)
        content.addSubview(label)
        panel.contentView = content
    }

    func show(below button: NSView) {
        guard let host = button.window, host.isVisible else { return }
        let anchor = host.convertToScreen(button.convert(button.bounds, to: nil))
        let screen = host.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? host.frame
        let size = panel.frame.size
        var origin = NSPoint(x: anchor.midX - size.width / 2, y: anchor.minY - size.height - 8)
        if origin.y < screen.minY { origin.y = anchor.maxY + 8 }
        origin.x = max(screen.minX, min(origin.x, screen.maxX - size.width))
        origin.y = max(screen.minY, min(origin.y, screen.maxY - size.height))
        panel.setFrameOrigin(origin)
        panel.level = NSWindow.Level(rawValue: host.level.rawValue + 1)
        if panel.parent !== host {
            panel.parent?.removeChildWindow(panel)
            host.addChildWindow(panel, ordered: .above)
        }
        panel.orderFrontRegardless()
    }

    func hide() { panel.orderOut(nil) }
}

final class FolderButton: NSButton {
    private var hoverArea: NSTrackingArea?
    private var pressOrigin: (pointer: NSPoint, window: NSPoint)?
    private var isDragging = false
    var didDrag: ((NSPoint) -> Void)?
    var hoverHint = HoverHint(text: "查看当前目录")
    var actionID = ""

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let hoverArea { removeTrackingArea(hoverArea) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect],
            owner: self, userInfo: nil
        )
        addTrackingArea(area)
        hoverArea = area
    }

    override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        layer?.backgroundColor = NSColor(srgbRed: 0.067, green: 0.329, blue: 0.247, alpha: 1).cgColor
        if pressOrigin == nil { hoverHint.show(below: self) }
    }

    override func mouseMoved(with event: NSEvent) {
        super.mouseMoved(with: event)
        if pressOrigin == nil { hoverHint.show(below: self) }
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        layer?.backgroundColor = NSColor(srgbRed: 0.09, green: 0.42, blue: 0.314, alpha: 1).cgColor
        hoverHint.hide()
    }

    override func mouseDown(with event: NSEvent) {
        hoverHint.hide()
        guard isEnabled, let window else { return }
        pressOrigin = (pointer: screenPoint(for: event, in: window), window: window.frame.origin)
        isDragging = false
        isHighlighted = true
    }

    override func mouseDragged(with event: NSEvent) {
        updateDrag(with: event)
    }

    override func mouseUp(with event: NSEvent) {
        guard pressOrigin != nil, let window else { return }
        defer {
            pressOrigin = nil
            isDragging = false
            isHighlighted = false
        }
        guard isEnabled, window.isVisible else { return }
        updateDrag(with: event)
        if isDragging {
            let origin = ButtonPlacement.constrained(window.frame.origin, size: window.frame.size,
                                                      screens: NSScreen.screens.map(\.visibleFrame))
            window.setFrameOrigin(origin)
            didDrag?(window.frame.origin)
        } else if bounds.contains(convert(event.locationInWindow, from: nil)) {
            sendAction(action, to: target)
        }
    }

    private func screenPoint(for event: NSEvent, in window: NSWindow) -> NSPoint {
        window.convertToScreen(NSRect(origin: event.locationInWindow, size: .zero)).origin
    }

    private func updateDrag(with event: NSEvent) {
        guard let pressOrigin, let window, window.isVisible else { return }
        let point = screenPoint(for: event, in: window)
        let dx = point.x - pressOrigin.pointer.x
        let dy = point.y - pressOrigin.pointer.y
        // Small hand movements stay a click; crossing the threshold latches a drag.
        if hypot(dx, dy) >= 4 { isDragging = true }
        guard isDragging else { return }
        hoverHint.hide()
        isHighlighted = false
        window.setFrameOrigin(NSPoint(x: pressOrigin.window.x + dx, y: pressOrigin.window.y + dy))
    }

    override func rightMouseDown(with event: NSEvent) {
        hoverHint.hide()
        super.rightMouseDown(with: event)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var panel: NSPanel!
    private var buttons: [FolderButton] = []
    private var folderButton: FolderButton { buttons[0] }
    private var definitions: [ActionDefinition] = []
    private var service: ActionService!
    private var taskWindow: TaskWindow?
    private var projectName = "项目"
    private var activeRuns: [ActionRun] = []
    private var pendingActions = Set<String>()
    private var serviceReady = false
    private var serviceError: String?
    private var serviceRefreshPending = false
    private var lastServiceRefresh = Date.distantPast
    private var lastStartedRun: ActionRun?
    private var statusItem: NSStatusItem!
    private var visibility: ProjectVisibility!
    private var placement: ButtonPlacement!
    private var refreshTimer: Timer?
    private var lastStateData: Data?
    private var selection: CodexProjectSelection?
    private var projectStateFile: URL!
    private var diagnosticsFile: URL?
    private var lastDiagnostics: Data?
    private var targetDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)

    func applicationDidFinishLaunching(_ notification: Notification) {
        let arguments = CommandLine.arguments
        func value(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else { return nil }
            return arguments[index + 1]
        }
        if let flag = arguments.firstIndex(of: "--directory"), arguments.indices.contains(flag + 1) {
            targetDirectory = URL(fileURLWithPath: arguments[flag + 1], isDirectory: true)
        } else if let configured = Bundle.main.object(forInfoDictionaryKey: "ProjectDirectory") as? String {
            targetDirectory = URL(fileURLWithPath: configured, isDirectory: true)
        }
        let projectID = value("--project-id") ?? (value("--directory") == nil
            ? Bundle.main.object(forInfoDictionaryKey: "ProjectID") as? String : nil)
        let defaults = value("--preferences-suite").flatMap(UserDefaults.init(suiteName:)) ?? .standard
        visibility = ProjectVisibility(binding: ProjectBinding(directory: targetDirectory, projectID: projectID), defaults: defaults)
        placement = ButtonPlacement(binding: visibility.binding, defaults: defaults)
        let codexHome = ProcessInfo.processInfo.environment["CODEX_HOME"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex", isDirectory: true)
        projectStateFile = value("--state-file").map { URL(fileURLWithPath: $0) }
            ?? codexHome.appendingPathComponent(".codex-global-state.json")
        diagnosticsFile = value("--diagnostics-file").map { URL(fileURLWithPath: $0) }
        let serviceScript = value("--service-script").map { URL(fileURLWithPath: $0) }
            ?? Bundle.main.resourceURL!.appendingPathComponent("ProjectActions/project_actions.py")
        #if arch(arm64)
        let runtimeArch = "arm64"
        #else
        let runtimeArch = "x86_64"
        #endif
        let embeddedPython = Bundle.main.resourceURL!.appendingPathComponent("Python-\(runtimeArch)/bin/python3")
        let defaultPython = FileManager.default.isExecutableFile(atPath: embeddedPython.path)
            ? embeddedPython.path : Bundle.main.object(forInfoDictionaryKey: "PythonExecutable") as? String
        service = ActionService(directory: targetDirectory, script: serviceScript,
                                python: value("--python") ?? defaultPython)

        let size: CGFloat = 64
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: size, height: size),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.isMovableByWindowBackground = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.acceptsMouseMovedEvents = true

        let content = NSView(frame: NSRect(x: 0, y: 0, width: size, height: size))
        panel.contentView = content
        struct LocalConfig: Decodable { let actions: [ActionDefinition] }
        let configURL = targetDirectory.appendingPathComponent(".project-actions/actions.json")
        if let data = try? Data(contentsOf: configURL), let config = try? JSONDecoder().decode(LocalConfig.self, from: data) {
            renderActions(config.actions)
        }
        panel.center()
        if let savedOrigin = placement.origin {
            panel.setFrameOrigin(ButtonPlacement.constrained(savedOrigin, size: panel.frame.size,
                                                             screens: NSScreen.screens.map(\.visibleFrame)))
        }
        NotificationCenter.default.addObserver(self, selector: #selector(screenParametersChanged),
                                               name: NSApplication.didChangeScreenParametersNotification, object: nil)

        // The status item survives hiding the panel, so hiding is always reversible.
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "folder.badge.gearshape", accessibilityDescription: "项目按钮")
            ?? NSImage(systemSymbolName: "folder", accessibilityDescription: "项目按钮")
        if statusItem.button?.image == nil { statusItem.button?.title = "目录" }
        statusItem.button?.toolTip = "项目按钮：显示或隐藏"
        statusItem.menu = makeControlMenu()

        if arguments.contains("--verify-drag") {
            DispatchQueue.main.async { self.verifyDrag() }
            return
        }
        if arguments.contains("--verify-lifecycle") {
            DispatchQueue.main.async { self.verifyLifecycle() }
            return
        }
        if arguments.contains("--verify-actions") {
            DispatchQueue.main.async { self.verifyActions() }
            return
        }
        refreshVisibility()
        if value("--snapshot") == nil {
            refreshTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
                self?.refreshVisibility()
                self?.refreshServiceIfNeeded()
            }
            refreshTimer?.tolerance = 0.1
        }

        if let flag = arguments.firstIndex(of: "--snapshot"), arguments.indices.contains(flag + 1) {
            guard let button = buttons.first else { preconditionFailure("Snapshot requires configured actions") }
            panel.orderFrontRegardless()
            let output = URL(fileURLWithPath: arguments[flag + 1])
            let verifyHover = arguments.contains("--verify-hover")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                if verifyHover {
                    let enter = NSEvent.enterExitEvent(
                        with: .mouseEntered, location: NSPoint(x: 32, y: 32),
                        modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                        windowNumber: self.panel.windowNumber, context: nil,
                        eventNumber: 0, trackingNumber: 0, userData: nil
                    )!
                    button.mouseEntered(with: enter)
                    let hint = button.hoverHint.panel
                    precondition(hint.isVisible, "Hover must show the hint window")
                    precondition(!self.panel.frame.intersects(hint.frame), "Hint must appear outside the button window")
                    precondition(hint.ignoresMouseEvents, "Hint must not intercept the pointer")
                    self.saveSnapshot(of: [self.panel, hint], to: output)
                    let exit = NSEvent.enterExitEvent(
                        with: .mouseExited, location: .zero,
                        modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                        windowNumber: self.panel.windowNumber, context: nil,
                        eventNumber: 1, trackingNumber: 0, userData: nil
                    )!
                    button.mouseExited(with: exit)
                    precondition(!hint.isVisible, "Leaving the button must hide the hint")
                    print("PASS: native enter/exit handlers show and hide a separate hint window outside the 64x64 button window.")
                    NSApp.terminate(nil)
                    return
                }
                content.displayIfNeeded()
                if let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds) {
                    content.cacheDisplay(in: content.bounds, to: bitmap)
                    if let png = bitmap.representation(using: .png, properties: [:]) {
                        try? png.write(to: output)
                    }
                }
                NSApp.terminate(nil)
            }
        } else {
            connectService()
        }
    }

    private func renderActions(_ actions: [ActionDefinition]) {
        guard definitions != actions else { return }
        for button in buttons {
            button.hoverHint.hide()
            panel.removeChildWindow(button.hoverHint.panel)
            button.removeFromSuperview()
        }
        definitions = actions
        buttons = []
        let maxColumns = max(1, min(8, Int(((NSScreen.main?.visibleFrame.width ?? 800) - 16) / 60)))
        let columns = min(maxColumns, max(1, actions.count))
        let rows = max(1, Int(ceil(Double(actions.count) / Double(columns))))
        let size = NSSize(width: 16 + columns * 48 + (columns - 1) * 12,
                          height: 16 + rows * 48 + (rows - 1) * 12)
        panel.setContentSize(size)
        panel.contentView?.setFrameSize(size)
        let symbols = ["folder": "folder", "play": "play.fill", "upload": "arrow.up.to.line",
                       "download": "arrow.down.to.line", "server": "server.rack", "file": "doc.text",
                       "history": "clock.arrow.circlepath"]
        for (index, definition) in actions.enumerated() {
            let button = FolderButton(frame: NSRect(x: 8 + (index % columns) * 60,
                y: Int(size.height) - 56 - (index / columns) * 60, width: 48, height: 48))
            button.actionID = definition.id
            button.hoverHint = HoverHint(text: definition.tooltip)
            let icon = definition.icon ?? "play"
            button.image = NSImage(systemSymbolName: symbols[icon] ?? icon, accessibilityDescription: definition.tooltip)
            if button.image == nil { button.image = NSImage(systemSymbolName: "play.fill", accessibilityDescription: definition.tooltip) }
            button.image = button.image?.withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 23, weight: .medium))
            button.title = ""
            button.isBordered = false
            button.bezelStyle = .regularSquare
            button.imagePosition = .imageOnly
            button.contentTintColor = .white
            button.wantsLayer = true
            button.layer?.cornerRadius = 12
            button.layer?.backgroundColor = NSColor(srgbRed: 0.09, green: 0.42, blue: 0.314, alpha: 1).cgColor
            button.setAccessibilityLabel(definition.tooltip)
            button.setAccessibilityHelp(definition.tooltip)
            button.target = self
            button.action = #selector(executeAction(_:))
            button.didDrag = { [weak self] origin in self?.placement.origin = origin; self?.refreshVisibility() }
            button.menu = makeControlMenu()
            panel.contentView?.addSubview(button)
            buttons.append(button)
        }
        panel.setFrameOrigin(ButtonPlacement.constrained(panel.frame.origin, size: size, screens: NSScreen.screens.map(\.visibleFrame)))
        updateButtonStates()
    }

    private func updateButtonStates() {
        for button in buttons {
            button.isEnabled = serviceReady && !pendingActions.contains(button.actionID)
            button.layer?.borderWidth = activeRuns.contains { $0.actionId == button.actionID && $0.isActive } ? 2 : 0
            button.layer?.borderColor = NSColor.systemBlue.cgColor
        }
    }

    private func acceptProject(_ project: ActionProject) {
        projectName = project.project.name
        serviceReady = true
        serviceError = nil
        activeRuns = project.runs.filter(\.isActive)
        renderActions(project.actions)
        updateButtonStates()
        statusItem.button?.toolTip = "\(projectName) · 项目按钮"
    }

    private func connectionFailed(_ error: Error) {
        serviceReady = false
        serviceError = error.localizedDescription
        statusItem.button?.toolTip = "项目按钮：\(error.localizedDescription)"
        updateButtonStates()
    }

    @objc private func connectService() {
        lastServiceRefresh = Date()
        service.connect { result in
            switch result {
            case .success(let project): self.acceptProject(project)
            case .failure(let error): self.connectionFailed(error)
            }
        }
    }

    private func refreshServiceIfNeeded() {
        guard Date().timeIntervalSince(lastServiceRefresh) > 3, !serviceRefreshPending else { return }
        lastServiceRefresh = Date()
        if !serviceReady { connectService(); return }
        serviceRefreshPending = true
        service.fetchProject { result in
            self.serviceRefreshPending = false
            switch result {
            case .success(let project): self.acceptProject(project)
            case .failure(let error): self.connectionFailed(error)
            }
        }
    }

    private func makeControlMenu() -> NSMenu {
        let menu = NSMenu()
        menu.delegate = self
        for (title, mode) in [("跟随项目自动显示", VisibilityMode.automatic), ("始终显示按钮", .shown), ("隐藏按钮", .hidden)] {
            let item = NSMenuItem(title: title, action: #selector(changeVisibility(_:)), keyEquivalent: "")
            item.representedObject = mode.rawValue
            item.target = self
            menu.addItem(item)
        }
        menu.addItem(.separator())
        let history = NSMenuItem(title: "查看任务记录", action: #selector(showHistory), keyEquivalent: "")
        history.target = self
        menu.addItem(history)
        let reconnect = NSMenuItem(title: "重新连接执行服务", action: #selector(connectService), keyEquivalent: "")
        reconnect.target = self
        menu.addItem(reconnect)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "退出程序", action: #selector(quitApp), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
        return menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.items.filter { $0.tag == 900 }.forEach { menu.removeItem($0) }
        if let serviceError {
            let error = NSMenuItem(title: serviceError, action: nil, keyEquivalent: "")
            error.tag = 900
            error.isEnabled = false
            menu.insertItem(error, at: 0)
        }
        for item in menu.items {
            if let raw = item.representedObject as? String {
                item.state = raw == visibility.mode.rawValue ? .on : .off
            }
            if item.action == #selector(showHistory) { item.isEnabled = serviceReady }
        }
    }

    @objc private func changeVisibility(_ item: NSMenuItem) {
        guard let raw = item.representedObject as? String, let mode = VisibilityMode(rawValue: raw) else { return }
        visibility.mode = mode
        refreshVisibility()
    }

    @objc private func screenParametersChanged() {
        let origin = ButtonPlacement.constrained(panel.frame.origin, size: panel.frame.size,
                                                 screens: NSScreen.screens.map(\.visibleFrame))
        panel.setFrameOrigin(origin)
        placement.origin = panel.frame.origin
    }

    private func refreshVisibility() {
        let data = try? Data(contentsOf: projectStateFile)
        if data != lastStateData {
            lastStateData = data
            selection = data.flatMap { try? JSONDecoder().decode(CodexProjectSelection.self, from: $0) }
        }
        let hostIsRunning = NSWorkspace.shared.runningApplications.contains { $0.bundleIdentifier == "com.openai.codex" && !$0.isTerminated }
        applyVisibility(visibility.shouldShow(selection: selection, hostIsRunning: hostIsRunning))
        writeDiagnostics(hostIsRunning: hostIsRunning)
    }

    private func applyVisibility(_ show: Bool) {
        if show {
            if !buttons.isEmpty && !panel.isVisible { panel.orderFrontRegardless() }
        } else {
            buttons.forEach { $0.hoverHint.hide() }
            panel.orderOut(nil)
        }
        taskWindow?.applyVisibility(show)
    }

    private func writeDiagnostics(hostIsRunning: Bool) {
        guard let diagnosticsFile else { return }
        let report: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier,
            "executable": Bundle.main.executableURL?.path ?? "",
            "mode": visibility.mode.rawValue,
            "directory": targetDirectory.path,
            "boundProjectID": visibility.binding.projectID ?? "",
            "selectedProjectID": selection?.selected?.projectId ?? "",
            "selectionMatches": selection?.matches(visibility.binding) == true,
            "hostIsRunning": hostIsRunning,
            "panelVisible": panel.isVisible,
            "panelOrigin": ["x": panel.frame.origin.x, "y": panel.frame.origin.y],
            "statusItemInstalled": statusItem != nil,
            "actionIDs": definitions.map(\.id), "serviceReady": serviceReady,
            "serviceOwned": service?.ownsService == true,
            "taskWindowVisible": taskWindow?.window.isVisible == true
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]),
              data != lastDiagnostics else { return }
        do {
            try data.write(to: diagnosticsFile, options: .atomic)
            lastDiagnostics = data
        } catch { fputs("Cannot write visibility diagnostics: \(error)\n", stderr) }
    }

    private func verifyDrag() {
        precondition(CommandLine.arguments.contains("--preferences-suite"), "Drag verification requires isolated preferences")
        defer {
            if let flag = CommandLine.arguments.firstIndex(of: "--preferences-suite") {
                visibility.defaults.removePersistentDomain(forName: CommandLine.arguments[flag + 1])
            }
            NSApp.terminate(nil)
        }
        let recorder = ButtonActionRecorder()
        folderButton.isEnabled = true
        folderButton.target = recorder
        folderButton.action = #selector(ButtonActionRecorder.clicked)
        visibility.mode = .shown
        panel.orderFrontRegardless()
        let screen = NSScreen.main!.visibleFrame
        panel.setFrameOrigin(NSPoint(x: screen.midX - 100, y: screen.midY - 100))
        let start = panel.frame.origin
        let pointer = NSPoint(x: start.x + 32, y: start.y + 32)
        func event(_ type: NSEvent.EventType, at screenPoint: NSPoint) -> NSEvent {
            let local = panel.convertFromScreen(NSRect(origin: screenPoint, size: .zero)).origin
            return NSEvent.mouseEvent(with: type, location: local, modifierFlags: [],
                                      timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: panel.windowNumber,
                                      context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
        }
        folderButton.mouseDown(with: event(.leftMouseDown, at: pointer))
        folderButton.mouseDragged(with: event(.leftMouseDragged, at: NSPoint(x: pointer.x + 1, y: pointer.y + 1)))
        folderButton.mouseUp(with: event(.leftMouseUp, at: NSPoint(x: pointer.x + 1, y: pointer.y + 1)))
        precondition(recorder.clicks == 1 && panel.frame.origin == start,
                     "Small movement must remain a click: clicks=\(recorder.clicks), origin=\(panel.frame.origin), expected=\(start)")
        folderButton.hoverHint.show(below: folderButton)
        folderButton.mouseDown(with: event(.leftMouseDown, at: pointer))
        let destination = NSPoint(x: pointer.x + 80, y: pointer.y + 60)
        folderButton.mouseDragged(with: event(.leftMouseDragged, at: destination))
        precondition(!folderButton.hoverHint.panel.isVisible, "Dragging must hide the hint")
        folderButton.mouseUp(with: event(.leftMouseUp, at: destination))
        let expected = NSPoint(x: start.x + 80, y: start.y + 60)
        precondition(panel.frame.origin == expected && recorder.clicks == 1, "Dragging must move the panel without executing its action")
        let reloaded = ButtonPlacement(binding: visibility.binding, defaults: visibility.defaults)
        precondition(reloaded.origin == expected, "Dragging must save a position that can be restored")
        // Returning the pointer to its starting point is still a drag, never a click.
        folderButton.mouseDown(with: event(.leftMouseDown, at: destination))
        folderButton.mouseDragged(with: event(.leftMouseDragged, at: NSPoint(x: destination.x + 20, y: destination.y)))
        folderButton.mouseUp(with: event(.leftMouseUp, at: destination))
        precondition(recorder.clicks == 1 && panel.frame.origin == expected, "A drag returning to its origin must not execute an action")
        panel.setFrameOrigin(start)
        panel.setFrameOrigin(ButtonPlacement.constrained(reloaded.origin!, size: panel.frame.size, screens: NSScreen.screens.map(\.visibleFrame)))
        precondition(panel.frame.origin == expected, "A recreated placement must restore the window position")
        let other = ButtonPlacement(binding: ProjectBinding(directory: URL(fileURLWithPath: "/tmp/other"), projectID: "other"), defaults: visibility.defaults)
        precondition(other.origin == nil, "Saved positions must be isolated per project")
        let displays = [NSRect(x: 0, y: 0, width: 1000, height: 800), NSRect(x: -800, y: 0, width: 800, height: 600)]
        precondition(ButtonPlacement.constrained(NSPoint(x: -600, y: 100), size: panel.frame.size, screens: displays) == NSPoint(x: -600, y: 100))
        let recovered = ButtonPlacement.constrained(NSPoint(x: -600, y: 100), size: panel.frame.size, screens: [displays[0]])
        precondition(recovered == NSPoint(x: 0, y: 100), "Removing a display must restore an accessible position")
        print("PASS: native click/drag handlers move the panel without firing its action, save/restore per-project positions, and recover from a removed display. Events were supplied directly, not through the desktop pointer.")
    }

    private func verifyLifecycle() {
        // Run against disposable fixtures and preferences, never the real Codex state.
        precondition(CommandLine.arguments.contains("--state-file") && CommandLine.arguments.contains("--preferences-suite"),
                     "Lifecycle verification requires isolated state and preferences")
        defer {
            if let flag = CommandLine.arguments.firstIndex(of: "--preferences-suite") {
                visibility.defaults.removePersistentDomain(forName: CommandLine.arguments[flag + 1])
            }
            NSApp.terminate(nil)
        }
        func select(_ id: String?, malformed: Bool = false) {
            let localID = visibility.binding.projectID ?? "bound-project"
            var fixture: [String: Any] = [
                "local-projects": [localID: ["rootPaths": [targetDirectory.path]], "other-project": ["rootPaths": ["/tmp/other-project"]]],
                "active-workspace-roots": [targetDirectory.path] // Deliberately stale.
            ]
            if let id { fixture["selected-project"] = ["type": "local", "projectId": id] }
            let data = malformed ? Data("{".utf8) : try! JSONSerialization.data(withJSONObject: fixture)
            try! data.write(to: projectStateFile, options: .atomic)
            refreshVisibility()
        }
        func choose(_ mode: VisibilityMode) {
            let item = statusItem.menu!.items.first { ($0.representedObject as? String) == mode.rawValue }!
            precondition(NSApp.sendAction(item.action!, to: item.target, from: item))
            menuNeedsUpdate(statusItem.menu!)
            precondition(item.state == .on)
        }
        precondition(NSWorkspace.shared.runningApplications.contains { $0.bundleIdentifier == "com.openai.codex" }, "Open Codex for native verification")
        choose(.automatic)
        let boundID = visibility.binding.projectID ?? "bound-project"
        select(boundID)
        precondition(panel.isVisible, "The bound project must show the panel")
        folderButton.hoverHint.show(below: folderButton)
        precondition(folderButton.hoverHint.panel.isVisible)
        select("other-project")
        precondition(!panel.isVisible && !folderButton.hoverHint.panel.isVisible, "Leaving the project must hide both windows")
        let sameDirectoryOtherID: [String: Any] = [
            "selected-project": ["type": "local", "projectId": "same-directory-other-project"],
            "local-projects": ["same-directory-other-project": ["rootPaths": [targetDirectory.path]]]
        ]
        try! JSONSerialization.data(withJSONObject: sameDirectoryOtherID).write(to: projectStateFile, options: .atomic)
        refreshVisibility()
        if visibility.binding.projectID != nil {
            precondition(!panel.isVisible, "Project ID must distinguish projects sharing a directory")
        }
        select(boundID)
        precondition(panel.isVisible, "Returning must restore the panel")
        choose(.hidden)
        precondition(!panel.isVisible && statusItem.menu != nil, "Hiding must keep a recovery menu")
        select("other-project")
        select(boundID)
        precondition(!panel.isVisible, "Manual hiding must persist across project switches")
        let reloaded = ProjectVisibility(binding: visibility.binding, defaults: visibility.defaults)
        precondition(reloaded.mode == .hidden, "Manual preference must survive controller recreation")
        let otherBinding = ProjectBinding(directory: URL(fileURLWithPath: "/tmp/independent-project"), projectID: "independent-project")
        precondition(ProjectVisibility(binding: otherBinding, defaults: visibility.defaults).mode == .automatic,
                     "Manual preferences must be scoped to one project")
        choose(.shown)
        select("other-project")
        precondition(panel.isVisible, "Always show must override project following")
        choose(.automatic)
        precondition(!panel.isVisible)
        select(nil)
        precondition(!panel.isVisible, "A projectless selection must hide the panel")
        select(boundID, malformed: true)
        precondition(!panel.isVisible, "An unreadable schema must hide the panel")
        try! FileManager.default.removeItem(at: projectStateFile)
        refreshVisibility()
        precondition(!panel.isVisible, "A missing state file must hide the panel")
        select(boundID)
        precondition(!visibility.shouldShow(selection: selection, hostIsRunning: false), "Quitting Codex must hide automatic mode")
        precondition(visibility.shouldShow(selection: selection, hostIsRunning: true), "Reopening Codex must restore the matching project")
        precondition(statusItem.menu!.items.last?.title == "退出程序")
        print("PASS: isolated project switches, return, stale workspace rejection, native panel/hint hiding, recovery menu actions, saved preferences, malformed/missing state, and host shutdown decisions.")
    }

    private func saveSnapshot(of windows: [NSWindow], to output: URL) {
        let frame = windows.dropFirst().reduce(windows[0].frame) { $0.union($1.frame) }
        let image = NSImage(size: frame.size)
        image.lockFocus()
        for window in windows {
            guard let view = window.contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
                preconditionFailure("Cannot capture native view")
            }
            view.displayIfNeeded()
            view.cacheDisplay(in: view.bounds, to: bitmap)
            let rendered = NSImage(size: view.bounds.size)
            rendered.addRepresentation(bitmap)
            rendered.draw(at: NSPoint(x: window.frame.minX - frame.minX, y: window.frame.minY - frame.minY),
                          from: .zero, operation: .sourceOver, fraction: 1)
        }
        image.unlockFocus()
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:]) else {
            preconditionFailure("Cannot encode native hover preview")
        }
        do { try png.write(to: output) }
        catch { preconditionFailure("Cannot save native hover preview: \(error)") }
    }

    @objc private func executeAction(_ button: FolderButton) {
        buttons.forEach { $0.hoverHint.hide() }
        if let run = activeRuns.first(where: { $0.actionId == button.actionID && $0.isActive }) {
            showTask(run.id)
            return
        }
        guard serviceReady, !pendingActions.contains(button.actionID) else { return }
        let id = button.actionID
        pendingActions.insert(id)
        updateButtonStates()
        service.start(id) { result in
            self.pendingActions.remove(id)
            switch result {
            case .success(let run):
                self.lastStartedRun = run
                self.activeRuns.append(run)
                self.showTask(run.id)
            case .failure(let error):
                let alert = NSAlert()
                alert.messageText = "任务未能启动"
                alert.informativeText = error.localizedDescription
                alert.runModal()
            }
            self.updateButtonStates()
        }
    }

    private func showTask(_ id: String? = nil) {
        guard let baseURL = service.baseURL else { return }
        if taskWindow == nil { taskWindow = TaskWindow() }
        taskWindow?.open(baseURL: baseURL, runID: id, projectName: projectName)
        refreshVisibility()
    }

    @objc private func showHistory() { showTask() }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        service?.shutdown()
    }

    private func verifyActions() {
        precondition(CommandLine.arguments.contains("--preferences-suite") && CommandLine.arguments.contains("--directory"),
                     "Action verification requires an isolated fixture project and preferences")
        visibility.mode = .shown
        refreshVisibility()
        let deadline = Date().addingTimeInterval(30)
        var clicked = false
        var inspecting = false
        var completed = false
        let timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { timer in
            precondition(Date() < deadline, "Native action/service/WebKit verification timed out: \(self.serviceError ?? "")")
            if self.serviceError != nil { preconditionFailure(self.serviceError!) }
            guard self.serviceReady else { return }
            if !clicked {
                precondition(self.buttons.count == 2 && self.definitions.map(\.id) == ["directory", "project-check"])
                precondition(self.panel.frame.width == 124 && self.buttons.allSatisfy { $0.frame.size == NSSize(width: 48, height: 48) })
                self.buttons[1].hoverHint.show(below: self.buttons[1])
                precondition(self.buttons[1].hoverHint.panel.isVisible)
                self.applyVisibility(false)
                precondition(self.buttons.allSatisfy { !$0.hoverHint.panel.isVisible })
                self.applyVisibility(true)
                clicked = true
                self.buttons[1].performClick(nil)
            }
            guard let run = self.lastStartedRun, !inspecting, !completed else { return }
            inspecting = true
            self.service.fetchProject { result in
                guard case .success(let project) = result else { preconditionFailure("Cannot inspect native-started task") }
                guard project.runs.contains(where: { $0.id == run.id && $0.status == "succeeded" }) else { inspecting = false; return }
                let script = "JSON.stringify({status:document.getElementById('run-status').textContent,nodes:document.querySelectorAll('.flow-node.succeeded').length,logs:document.getElementById('logs').textContent,desktop:document.body.classList.contains('desktop')})"
                self.taskWindow!.webView.evaluateJavaScript(script) { value, error in
                    guard error == nil, let value = value as? String, let data = value.data(using: .utf8),
                          let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          result["status"] as? String == "已完成", result["nodes"] as? Int == 3 else { inspecting = false; return }
                    precondition(result["desktop"] as? Bool == true)
                    precondition((result["logs"] as? String)?.contains(self.targetDirectory.path) == true)
                    precondition(self.taskWindow!.window.isVisible)
                    self.applyVisibility(false)
                    precondition(!self.taskWindow!.window.isVisible)
                    self.applyVisibility(true)
                    precondition(self.taskWindow!.window.isVisible)
                    self.taskWindow!.window.performClose(nil)
                    self.applyVisibility(false); self.applyVisibility(true)
                    precondition(!self.taskWindow!.window.isVisible, "A closed task window must stay closed")
                    // Change the same JSON while the native app runs, then load through the shared API.
                    let file = self.targetDirectory.appendingPathComponent(".project-actions/actions.json")
                    var config = try! JSONSerialization.jsonObject(with: Data(contentsOf: file)) as! [String: Any]
                    var actions = config["actions"] as! [[String: Any]]
                    var extra = actions[0]
                    extra["id"] = "extra"
                    extra["tooltip"] = "热加载按钮"
                    actions.append(extra)
                    config["actions"] = actions
                    try! JSONSerialization.data(withJSONObject: config).write(to: file, options: .atomic)
                    completed = true
                    self.service.fetchProject { result in
                        guard case .success(let updated) = result else { preconditionFailure("Config reload failed") }
                        self.acceptProject(updated)
                        precondition(self.buttons.count == 3 && self.buttons[2].accessibilityLabel() == "热加载按钮")
                        self.buttons[2].hoverHint.show(below: self.buttons[2])
                        precondition(self.buttons[2].hoverHint.panel.isVisible)
                        self.applyVisibility(false)
                        precondition(self.buttons.allSatisfy { !$0.hoverHint.panel.isVisible })
                        print("PASS: JSON-generated native buttons, real native action → shared Shell service, embedded WebKit logs/3-node flow, task window hide/restore/close, and live JSON reload.")
                        timer.invalidate()
                        if let flag = CommandLine.arguments.firstIndex(of: "--preferences-suite") {
                            self.visibility.defaults.removePersistentDomain(forName: CommandLine.arguments[flag + 1])
                        }
                        NSApp.terminate(nil)
                    }
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        connectService()
    }

    @objc private func quitApp() { NSApp.terminate(nil) }
}

final class ButtonActionRecorder: NSObject {
    var clicks = 0
    @objc func clicked() { clicks += 1 }
}

@main
enum DirectoryButtonApp {
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.setActivationPolicy(.accessory)
        application.run()
    }
}
