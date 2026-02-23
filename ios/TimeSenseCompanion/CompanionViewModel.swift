import Foundation

@MainActor
final class CompanionViewModel: ObservableObject {
    @Published var hasAccess = false
    @Published var isSyncing = false
    @Published var lastSyncAt: Date?
    @Published var lastUpserted = 0
    @Published var lastError = ""

    private let settings: SettingsStore
    private let eventKit = EventKitProvider()
    private let syncService = SyncService()

    init(settings: SettingsStore) {
        self.settings = settings
    }

    var lastSyncAtDisplay: String {
        guard let lastSyncAt else { return "Never" }
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: lastSyncAt)
    }

    func requestAccessIfNeeded() async {
        if hasAccess { return }
        let granted = await eventKit.requestAccess()
        hasAccess = granted
        if !granted {
            lastError = "Calendar access is off. Enable in Settings."
        }
    }

    func syncNow() {
        Task { await performSync() }
    }

    private func syncRange() -> (Date, Date) {
        let now = Date()
        let start = Calendar.current.date(byAdding: .day, value: -7, to: now) ?? now
        let end = Calendar.current.date(byAdding: .day, value: 30, to: now) ?? now
        return (start, end)
    }

    private func performSync() async {
        lastError = ""
        isSyncing = true
        defer { isSyncing = false }

        let granted = await eventKit.requestAccess()
        hasAccess = granted
        if !granted {
            lastError = "Calendar access is off. Enable in Settings."
            return
        }

        let (rangeStart, rangeEnd) = syncRange()
        let events = eventKit.fetchEvents(rangeStart: rangeStart, rangeEnd: rangeEnd)

        do {
            let response = try await syncService.sync(
                baseUrl: settings.baseUrl,
                token: settings.token,
                rangeStart: rangeStart,
                rangeEnd: rangeEnd,
                events: events
            )
            lastSyncAt = Date()
            lastUpserted = response.upserted
        } catch {
            lastError = error.localizedDescription
        }
    }
}

