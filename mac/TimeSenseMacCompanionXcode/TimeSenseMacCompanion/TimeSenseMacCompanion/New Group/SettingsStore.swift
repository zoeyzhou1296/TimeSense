import Foundation

final class SettingsStore: ObservableObject {
    @Published var baseUrl: String {
        didSet { UserDefaults.standard.set(baseUrl, forKey: "baseUrl") }
    }
    @Published var token: String {
        didSet { UserDefaults.standard.set(token, forKey: "token") }
    }

    init() {
        let storedBase = UserDefaults.standard.string(forKey: "baseUrl")
        let storedToken = UserDefaults.standard.string(forKey: "token")
        self.baseUrl = storedBase ?? "http://127.0.0.1:8000"
        self.token = storedToken ?? ""
    }
}

