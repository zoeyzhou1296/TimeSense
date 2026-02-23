import Foundation

enum SyncError: LocalizedError {
    case invalidBaseUrl
    case requestFailed(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseUrl:
            return "Invalid server URL."
        case .requestFailed(let code, let body):
            return "Sync failed (\(code)): \(body)"
        }
    }
}

final class SyncService {
    func sync(
        baseUrl: String,
        token: String,
        rangeStart: Date,
        rangeEnd: Date,
        events: [PlannedEventPayload]
    ) async throws -> AppleSyncResponse {
        guard let url = URL(string: baseUrl.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw SyncError.invalidBaseUrl
        }
        let endpoint = url.appendingPathComponent("api/apple_sync/planned_upsert")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let payload = AppleSyncPayload(rangeStart: rangeStart, rangeEnd: rangeEnd, events: events)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        request.httpBody = try encoder.encode(payload)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SyncError.requestFailed(-1, "No response")
        }
        if http.statusCode < 200 || http.statusCode >= 300 {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw SyncError.requestFailed(http.statusCode, body)
        }

        let decoder = JSONDecoder()
        return try decoder.decode(AppleSyncResponse.self, from: data)
    }
}

