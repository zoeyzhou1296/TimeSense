import SwiftUI

@main
struct TimeSenseCompanionApp: App {
    @StateObject private var settings = SettingsStore()
    @StateObject private var viewModel: CompanionViewModel

    init() {
        let settings = SettingsStore()
        _settings = StateObject(wrappedValue: settings)
        _viewModel = StateObject(wrappedValue: CompanionViewModel(settings: settings))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(viewModel: viewModel, settings: settings)
        }
    }
}

