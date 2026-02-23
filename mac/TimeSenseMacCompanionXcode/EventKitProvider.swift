import EventKit
import Foundation

final class EventKitProvider {
    private let store = EKEventStore()

    func requestAccess() async -> Bool {
        await withCheckedContinuation { continuation in
            if #available(macOS 14.0, *) {
                store.requestFullAccessToEvents { granted, _ in
                    continuation.resume(returning: granted)
                }
            } else {
                store.requestAccess(to: .event) { granted, _ in
                    continuation.resume(returning: granted)
                }
            }
        }
    }

    func fetchEvents(rangeStart: Date, rangeEnd: Date) -> [PlannedEventPayload] {
        // First, refresh the event store to get latest data
        store.refreshSourcesIfNecessary()
        
        // Debug: List all calendars and their sources
        let allCalendars = store.calendars(for: .event)
        print("[EventKit] === CALENDAR DEBUG ===")
        print("[EventKit] Total calendars accessible: \(allCalendars.count)")
        for cal in allCalendars {
            print("[EventKit]   - \(cal.title) (source: \(cal.source.title), type: \(cal.source.sourceType.rawValue))")
        }
        
        let predicate = store.predicateForEvents(withStart: rangeStart, end: rangeEnd, calendars: nil)
        let events = store.events(matching: predicate)
        
        // Debug: print range and sample events
        let formatter = ISO8601DateFormatter()
        print("[EventKit] Fetching events from \(formatter.string(from: rangeStart)) to \(formatter.string(from: rangeEnd))")
        print("[EventKit] Found \(events.count) events")
        
        // Group events by year
        var eventsByYear: [Int: Int] = [:]
        for ev in events {
            let year = Calendar.current.component(.year, from: ev.startDate)
            eventsByYear[year, default: 0] += 1
        }
        print("[EventKit] Events by year:")
        for (year, count) in eventsByYear.sorted(by: { $0.key > $1.key }) {
            print("[EventKit]   \(year): \(count) events")
        }
        
        // Find and log the newest event
        if let newest = events.max(by: { $0.startDate < $1.startDate }) {
            print("[EventKit] Newest event: \(newest.title ?? "No title") at \(formatter.string(from: newest.startDate)) from calendar: \(newest.calendar.title)")
        }
        
        // Check for events in January 2026 specifically
        let jan2026Start = Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 1))!
        let jan2026End = Calendar.current.date(from: DateComponents(year: 2026, month: 2, day: 1))!
        let jan2026Predicate = store.predicateForEvents(withStart: jan2026Start, end: jan2026End, calendars: nil)
        let jan2026Events = store.events(matching: jan2026Predicate)
        print("[EventKit] Events specifically in Jan 2026: \(jan2026Events.count)")
        for ev in jan2026Events.prefix(5) {
            print("[EventKit]   - \(ev.title ?? "No title") on \(formatter.string(from: ev.startDate))")
        }
        
        return events.map { ev in
            PlannedEventPayload(
                externalId: ev.eventIdentifier ?? UUID().uuidString,
                title: (ev.title ?? "Planned").trimmingCharacters(in: .whitespacesAndNewlines),
                startAt: ev.startDate,
                endAt: ev.endDate,
                isAllDay: ev.isAllDay,
                sourceCalendarName: ev.calendar.title
            )
        }
    }
    
    /// Fetch events for a smaller chunk (used to work around EventKit's large range limitations)
    func fetchEventsChunk(rangeStart: Date, rangeEnd: Date) -> [PlannedEventPayload] {
        let predicate = store.predicateForEvents(withStart: rangeStart, end: rangeEnd, calendars: nil)
        let events = store.events(matching: predicate)
        
        return events.map { ev in
            PlannedEventPayload(
                externalId: ev.eventIdentifier ?? UUID().uuidString,
                title: (ev.title ?? "Planned").trimmingCharacters(in: .whitespacesAndNewlines),
                startAt: ev.startDate,
                endAt: ev.endDate,
                isAllDay: ev.isAllDay,
                sourceCalendarName: ev.calendar.title
            )
        }
    }
}

