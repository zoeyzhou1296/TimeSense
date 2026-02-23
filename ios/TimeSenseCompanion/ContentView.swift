import SwiftUI

struct ContentView: View {
    @ObservedObject var viewModel: CompanionViewModel
    @ObservedObject var settings: SettingsStore

    var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Status")) {
                    HStack {
                        Text("Calendar Access")
                        Spacer()
                        Text(viewModel.hasAccess ? "Allowed" : "Denied")
                            .foregroundStyle(viewModel.hasAccess ? .green : .red)
                    }
                    HStack {
                        Text("Last Sync")
                        Spacer()
                        Text(viewModel.lastSyncAtDisplay)
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Events Synced")
                        Spacer()
                        Text("\(viewModel.lastUpserted)")
                            .foregroundStyle(.secondary)
                    }
                    if !viewModel.lastError.isEmpty {
                        Text(viewModel.lastError)
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                    Button(viewModel.isSyncing ? "Syncing..." : "Sync now") {
                        viewModel.syncNow()
                    }
                    .disabled(viewModel.isSyncing)
                }

                Section(header: Text("Server")) {
                    TextField("Base URL", text: $settings.baseUrl)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    SecureField("Companion Token (optional)", text: $settings.token)
                        .textInputAutocapitalization(.never)
                }

                Section(header: Text("Privacy")) {
                    Text("Title only. No notes, location, or attendees are read or sent.")
                        .font(.footnote)
                }
            }
            .navigationTitle("TimeSense Sync")
            .task {
                await viewModel.requestAccessIfNeeded()
            }
        }
    }
}

