// Talking to the MACD engine: base URL in UserDefaults, API token in the Keychain, read-only GET requests.
import Foundation
import Observation
import Security
import SwiftUI

struct APIError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct EngineAPI {
    let base: URL
    let token: String

    func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        guard var comps = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw APIError("Bad engine address")
        }
        if !query.isEmpty { comps.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) } }
        guard let url = comps.url else { throw APIError("Bad engine address") }
        var request = URLRequest(url: url, timeoutInterval: 25)
        request.setValue(token, forHTTPHeaderField: "X-Macd-Token")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch let error as URLError where error.code == .cannotConnectToHost || error.code == .timedOut
                    || error.code == .cannotFindHost || error.code == .networkConnectionLost {
            throw APIError("Can't reach the engine at \(base.absoluteString). Is the macd-mini-parallel stack running?")
        }
        guard let http = response as? HTTPURLResponse else { throw APIError("No HTTP response") }
        if http.statusCode == 401 { throw APIError("The engine refused the API token. Check it in Settings.") }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw APIError("Engine answered \(http.statusCode)\(detail.map { ": \($0)" } ?? "")")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError("Unexpected reply from \(path): \(error)")
        }
    }
}

enum Keychain {
    private static let service = "local.macdmini.client"
    private static let account = "macd-api-token"
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func read() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ value: String) {
        SecItemDelete(query as CFDictionary)
        var q = query
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }

    static func clear() { SecItemDelete(query as CFDictionary) }
}

enum AppTab: Hashable { case lanes, book, signals, sectors, settings }

@MainActor @Observable
final class AppModel {
    static let defaultBase = "http://localhost:8201"

    private(set) var baseURL: String
    private(set) var hasToken: Bool
    /// Bumped when the address or token changes, so every screen's polling restarts.
    private(set) var version = 0
    var tab: AppTab = .lanes

    var health: Health?
    var healthError: String?

    init() {
        #if DEBUG && targetEnvironment(simulator)
        // UI-test hook for simulator runs against a local mock engine: `simctl launch` with
        // SIMCTL_CHILD_MACD_UITEST_BASE / _TOKEN / _TAB. Compiled out of release and device builds.
        let env = ProcessInfo.processInfo.environment
        if let base = env["MACD_UITEST_BASE"] { UserDefaults.standard.set(base, forKey: "engineBaseURL") }
        if let token = env["MACD_UITEST_TOKEN"], !token.isEmpty { Keychain.save(token) }
        switch env["MACD_UITEST_TAB"] {
        case "book": tab = .book
        case "signals": tab = .signals
        case "sectors": tab = .sectors
        case "settings": tab = .settings
        default: break
        }
        #endif
        baseURL = UserDefaults.standard.string(forKey: "engineBaseURL") ?? AppModel.defaultBase
        hasToken = !(Keychain.read() ?? "").isEmpty
    }

    func setBaseURL(_ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        baseURL = trimmed.isEmpty ? AppModel.defaultBase : trimmed
        UserDefaults.standard.set(baseURL, forKey: "engineBaseURL")
        version += 1
    }

    func saveToken(_ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        Keychain.save(trimmed)
        hasToken = true
        version += 1
    }

    func clearToken() {
        Keychain.clear()
        hasToken = false
        health = nil
        version += 1
    }

    func api() throws -> EngineAPI {
        guard let token = Keychain.read(), !token.isEmpty else {
            throw APIError("No API token yet. Add the engine's MACD_API_TOKEN in Settings.")
        }
        guard let url = URL(string: baseURL), url.scheme != nil, url.host != nil else {
            throw APIError("The engine address “\(baseURL)” isn't a URL. Fix it in Settings.")
        }
        return EngineAPI(base: url, token: token)
    }

    func refreshHealth() async {
        do {
            health = try await api().get("api/system/health")
            healthError = nil
        } catch is CancellationError {
        } catch {
            if (error as? URLError)?.code == .cancelled { return }
            healthError = error.localizedDescription
        }
    }
}

/// One screen's data: last good value, last error, and when it was fetched.
@MainActor @Observable
final class Loader<T> {
    var value: T?
    var error: String?
    var updated: Date?

    func load(_ fetch: () async throws -> T) async {
        do {
            value = try await fetch()
            error = nil
            updated = .now
        } catch is CancellationError {
        } catch {
            if (error as? URLError)?.code == .cancelled { return }
            self.error = error.localizedDescription
        }
    }
}

extension View {
    /// Runs `action` now and then every `seconds` while the view is on screen; restarts when `id` changes.
    func polling<ID: Equatable>(every seconds: Double, id: ID, _ action: @escaping @MainActor () async -> Void) -> some View {
        task(id: id) {
            while !Task.isCancelled {
                await action()
                try? await Task.sleep(for: .seconds(seconds))
            }
        }
    }
}
