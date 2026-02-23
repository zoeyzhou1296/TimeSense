import EventKit
import Foundation

final class EventKitProvider {
    private let store = EKEventStore()

    func requestAccess() async -> Bool {
        await withCheckedContinuation { continuation in
            store.requestAccess(to: .event) { granted, _ in
                continuation.resume(returning: granted)
            }
        }
    }

    func fetchEvents(rangeStart: Date, rangeEnd: Date) -> [PlannedEventPayload] {
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

