import SwiftUI

struct SyncView: View {
    @StateObject var viewModel: SyncViewModel

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Text("TimeSense")
                    .font(.headline)
                Spacer()
            }

            VStack(alignment: .leading, spacing: 6) {
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
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("Server")
                    .font(.subheadline)
                TextField("Base URL", text: $viewModel.baseUrl, prompt: Text("e.g. http://127.0.0.1:8000"))
                SecureField("Companion Token (optional)", text: $viewModel.token)
            }

            HStack {
                Button(viewModel.isSyncing ? "Syncing..." : "Sync now") {
                    viewModel.syncNow()
                }
                .disabled(viewModel.isSyncing)
                Spacer()
            }

            Text("Title only. No notes, location, or attendees.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .frame(width: 360)
        .padding(12)
        .task {
            await viewModel.requestAccessIfNeeded()
            viewModel.startAutoSync()
        }
    }
}

