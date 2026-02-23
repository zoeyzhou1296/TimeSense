import AppKit
import SwiftUI

@main
struct TimeSenseMacCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var settings = SettingsStore()
    @StateObject private var viewModel: SyncViewModel

    init() {
        let settings = SettingsStore()
        _settings = StateObject(wrappedValue: settings)
        _viewModel = StateObject(wrappedValue: SyncViewModel(settings: settings))
    }

    var body: some Scene {
        WindowGroup {
            SyncView(viewModel: viewModel)
        }
        Settings {
            EmptyView()
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var menuBarController: MenuBarController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Use a regular app during setup to ensure visibility; we can switch to .accessory later.
        NSApp.setActivationPolicy(.regular)
        menuBarController = MenuBarController()
    }
}

