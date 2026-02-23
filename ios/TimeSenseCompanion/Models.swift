import Foundation

struct PlannedEventPayload: Codable {
    let externalId: String
    let title: String
    let startAt: Date
    let endAt: Date
    let isAllDay: Bool
    let sourceCalendarName: String

    enum CodingKeys: String, CodingKey {
        case externalId = "external_id"
        case title
        case startAt = "start_at"
        case endAt = "end_at"
        case isAllDay = "is_all_day"
        case sourceCalendarName = "source_calendar_name"
    }
}

struct AppleSyncPayload: Codable {
    let rangeStart: Date
    let rangeEnd: Date
    let events: [PlannedEventPayload]

    enum CodingKeys: String, CodingKey {
        case rangeStart = "range_start"
        case rangeEnd = "range_end"
        case events
    }
}

struct AppleSyncResponse: Codable {
    let ok: Bool
    let deleted: Int
    let upserted: Int
}

