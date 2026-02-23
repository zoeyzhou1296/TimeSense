import Foundation

@MainActor
final class SyncViewModel: ObservableObject {
    @Published var hasAccess = false
    @Published var isSyncing = false
    @Published var lastSyncAt: Date?
    @Published var lastUpserted = 0
    @Published var lastError = ""

    let settings: SettingsStore
    @Published var baseUrl: String {
        didSet { settings.baseUrl = baseUrl }
    }
    @Published var token: String {
        didSet { settings.token = token }
    }
    private let eventKit = EventKitProvider()
    private let syncService = SyncService()
    private var autoTimer: Timer?
    private let lastSyncKey = "timesense.lastSyncAt"

    init(settings: SettingsStore) {
        self.settings = settings
        self.baseUrl = settings.baseUrl
        self.token = settings.token
        if let stored = UserDefaults.standard.object(forKey: lastSyncKey) as? Date {
            self.lastSyncAt = stored
        }
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

    func startAutoSync() {
        if autoTimer != nil { return }
        // Sync at least once per day; run every 6 hours for safety.
        autoTimer = Timer.scheduledTimer(withTimeInterval: 6 * 60 * 60, repeats: true) { [weak self] _ in
            self?.syncNow()
        }
        if let last = lastSyncAt {
            if Date().timeIntervalSince(last) > 20 * 60 * 60 {
                syncNow()
            }
        } else {
            syncNow()
        }
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

        // Sync in monthly chunks to work around EventKit's limitation with large date ranges
        let now = Date()
        let calendar = Calendar.current
        
        // Start from Jan 1, 2025, end 1 year in future
        let overallStart = calendar.date(from: DateComponents(year: 2025, month: 1, day: 1)) ?? now
        let overallEnd = calendar.date(byAdding: .year, value: 1, to: now) ?? now
        
        var allEvents: [PlannedEventPayload] = []
        var currentStart = overallStart
        
        print("[Sync] Fetching events in monthly chunks from \(overallStart) to \(overallEnd)")
        
        // Fetch in 3-month chunks
        while currentStart < overallEnd {
            let chunkEnd = calendar.date(byAdding: .month, value: 3, to: currentStart) ?? overallEnd
            let actualEnd = min(chunkEnd, overallEnd)
            
            let chunkEvents = eventKit.fetchEventsChunk(rangeStart: currentStart, rangeEnd: actualEnd)
            print("[Sync] Chunk \(currentStart) to \(actualEnd): \(chunkEvents.count) events")
            allEvents.append(contentsOf: chunkEvents)
            
            currentStart = actualEnd
        }
        
        // Deduplicate by external_id
        var seen = Set<String>()
        let uniqueEvents = allEvents.filter { ev in
            if seen.contains(ev.externalId) {
                return false
            }
            seen.insert(ev.externalId)
            return true
        }
        
        print("[Sync] Total unique events: \(uniqueEvents.count)")
        
        // Count events by year for debugging
        var byYear: [Int: Int] = [:]
        for ev in uniqueEvents {
            let year = calendar.component(.year, from: ev.startAt)
            byYear[year, default: 0] += 1
        }
        print("[Sync] Events by year after chunked fetch:")
        for (year, count) in byYear.sorted(by: { $0.key > $1.key }) {
            print("[Sync]   \(year): \(count)")
        }

        do {
            let response = try await syncService.sync(
                baseUrl: baseUrl,
                token: token,
                rangeStart: overallStart,
                rangeEnd: overallEnd,
                events: uniqueEvents
            )
            lastSyncAt = Date()
            UserDefaults.standard.set(lastSyncAt, forKey: lastSyncKey)
            lastUpserted = response.upserted
            print("[Sync] Sync complete: \(response.upserted) upserted, \(response.deleted) deleted")
        } catch {
            lastError = error.localizedDescription
            print("[Sync] Error: \(error.localizedDescription)")
        }
    }
}

