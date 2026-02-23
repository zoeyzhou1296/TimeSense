import AppKit
import SwiftUI

final class MenuBarController {
    private let statusItem: NSStatusItem
    private let popover: NSPopover
    private var eventMonitor: Any?

    init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        popover = NSPopover()
        popover.behavior = .transient

        if let button = statusItem.button {
            button.title = "⏱"
            button.action = #selector(togglePopover(_:))
            button.target = self
        }

        let root = ContentView()
        popover.contentViewController = NSHostingController(rootView: root)

        // Close popover when clicking outside
        eventMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            self?.closePopover()
        }
    }

    deinit {
        if let eventMonitor {
            NSEvent.removeMonitor(eventMonitor)
        }
    }

    @objc private func togglePopover(_ sender: AnyObject?) {
        if popover.isShown {
            closePopover()
        } else {
            showPopover()
        }
    }

    private func showPopover() {
        guard let button = statusItem.button else { return }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func closePopover() {
        popover.performClose(nil)
    }
}

private struct ContentView: View {
    // You can override this later via user defaults / settings UI.
    private let url = URL(string: "http://127.0.0.1:8000/")!

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Text("TimeSense")
                    .font(.headline)
                Spacer()
            }

            WebView(url: url)
                .frame(width: 420, height: 520)
        }
        .padding(10)
    }
}


