// Broker logins: Fyers through the MACD engine (/api/auth/fyers/*), Upstox through the platform console (:8410).
// The owner types every PIN, secret and pasted token; this screen sends each one once, to that service only, and
// never stores, logs or shows it. The in-app login uses a private web view whose cookies vanish when it closes.
import SwiftUI
import WebKit

struct FyersSettings: Decodable {
    struct Broker: Decodable {
        let status: String?
        let tokenExpiresAt: String?
        let tokenExpired: Bool?
        let socketAuthFailed: Bool?
        let error: String?
    }
    let fyersClientId: String?
    let fyersRedirectUri: String?
    let fyersSecretConfigured: Bool?
    let fyersAccessTokenConfigured: Bool?
    let fyersRefreshTokenConfigured: Bool?
    let fyersPinConfigured: Bool?
    let broker: Broker?
}

struct ConsoleLogins: Decodable {
    struct Upstox: Decodable {
        let status: String?
        let detail: String?
        let tokenPresent: Bool?
        let tokenExpired: Bool?
        let tokenExpiresAt: String?
        let redirectUri: String?
        let fields: [String: Bool]?
        let validation: String?
    }
    let upstox: Upstox?
}

private struct AuthURL: Decodable { let authUrl: String; let redirectUri: String? }
private struct FyersInstall: Decodable { let connectState: String?; let acceptedTokenExpiresAt: String? }

/// A login page to show, and the redirect it should end on.
struct BrokerLogin: Identifiable {
    enum Broker { case fyers, upstox }
    let broker: Broker
    let url: URL
    let redirect: URL?
    let param: String             // the query item carrying the code: auth_code (Fyers) or code (Upstox)
    var id: String { url.absoluteString }
    var title: String { broker == .fyers ? "Fyers login" : "Upstox login" }

    /// Only the broker's own https login pages (plus localhost in simulator debug builds, for the mock engine).
    static func trusted(_ url: URL, domain: String) -> Bool {
        let host = (url.host ?? "").lowercased()
        #if DEBUG && targetEnvironment(simulator)
        if host == "localhost" || host == "127.0.0.1" { return true }
        #endif
        return url.scheme == "https" && (host == domain || host.hasSuffix("." + domain))
    }
}

struct BrokersView: View {
    @Environment(AppModel.self) private var model
    @State private var fyers = Loader<FyersSettings>()
    @State private var logins = Loader<ConsoleLogins>()
    @State private var login: BrokerLogin?
    @State private var busy: String?
    @State private var fyersResult: Outcome?
    @State private var upstoxResult: Outcome?
    @State private var confirmRefresh = false

    @State private var fyersPaste = ""
    @State private var fyersAppId = ""
    @State private var fyersRedirect = ""
    @State private var fyersSecret = ""
    @State private var fyersPin = ""
    @State private var upstoxPaste = ""
    @State private var upstoxKey = ""
    @State private var upstoxSecret = ""
    @State private var upstoxRedirect = ""
    @State private var upstoxAnalytics = ""

    struct Outcome { let ok: Bool; let text: String }

    var body: some View {
        NavigationStack {
            Form {
                fyersSections
                upstoxSections
                Section {
                    Text("Paper trading only: these logins feed market data to the paper lanes. Nothing here places orders.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Brokers")
            .refreshable { await load() }
            .polling(every: 30, id: model.version) { await load() }
            .sheet(item: $login) { l in
                BrokerLoginSheet(login: l) { redirect in
                    login = nil
                    Task { await finish(l.broker, redirect: redirect) }
                }
            }
            .confirmationDialog("Ask Fyers for today's token with the saved refresh token and PIN?", isPresented: $confirmRefresh,
                                titleVisibility: .visible) {
                Button("Refresh the Fyers token") { Task { await refreshFyers() } }
            }
        }
    }

    // MARK: Fyers

    @ViewBuilder
    private var fyersSections: some View {
        let s = fyers.value
        Section {
            if let s {
                LabeledContent("Feed") { StatusText(status: s.broker?.status, bad: s.broker?.tokenExpired == true || s.broker?.socketAuthFailed == true) }
                LabeledContent("Access token", value: tokenLine(saved: s.fyersAccessTokenConfigured, expired: s.broker?.tokenExpired,
                                                                expires: s.broker?.tokenExpiresAt))
                LabeledContent("Refresh token", value: s.fyersRefreshTokenConfigured == true ? "saved" : "none")
                LabeledContent("PIN", value: s.fyersPinConfigured == true ? "saved" : "not saved")
                if s.broker?.socketAuthFailed == true {
                    Text("Fyers refused the feed socket's token while REST still took it. Log in again.").font(.caption).foregroundStyle(.orange)
                }
                if let err = s.broker?.error, !err.isEmpty { Text(err).font(.caption).foregroundStyle(.orange) }
            } else if let err = fyers.error {
                Label(err, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
            } else {
                ProgressView()
            }
            if let fyersResult { OutcomeText(outcome: fyersResult) }
        } header: {
            Text("Fyers · MACD engine")
        } footer: {
            Text("Fyers tokens last until about 06:00 IST the next morning. Refresh uses the saved refresh token and PIN; otherwise log in.")
        }

        Section {
            Button { confirmRefresh = true } label: { BusyLabel(title: "Refresh token now", systemImage: "arrow.clockwise", busy: busy == "fyers-refresh") }
                .disabled(busy != nil || s?.fyersRefreshTokenConfigured != true || s?.fyersPinConfigured != true)
            Button { Task { await openLogin(.fyers) } } label: { BusyLabel(title: "Log in to Fyers", systemImage: "person.badge.key", busy: busy == "fyers-url") }
                .disabled(busy != nil || !model.hasToken)
        } footer: {
            if s != nil && s?.fyersPinConfigured != true {
                Text("Refresh needs the Fyers PIN saved under App details below.")
            } else {
                Text("Log in opens Fyers in a private window here; after the login the app hands the auth code to the engine.")
            }
        }

        Section {
            SecureField("Redirect address, auth code or access token", text: $fyersPaste)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
            Button { Task { await pasteFyers() } } label: { BusyLabel(title: "Send to the engine", systemImage: "paperplane", busy: busy == "fyers-paste") }
                .disabled(busy != nil || fyersPaste.trimmingCharacters(in: .whitespacesAndNewlines).count < 10)
        } header: {
            Text("Logged in elsewhere?")
        } footer: {
            Text("Paste the address Fyers redirected to (or its auth code, or an access token). The engine checks it with Fyers before saving.")
        }

        Section {
            DisclosureGroup("App details") {
                TextField("App ID (e.g. XXXX-100)", text: $fyersAppId).textInputAutocapitalization(.characters).autocorrectionDisabled()
                TextField("Redirect URL", text: $fyersRedirect).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField(s?.fyersSecretConfigured == true ? "Secret (saved; leave blank to keep)" : "Secret", text: $fyersSecret)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField(s?.fyersPinConfigured == true ? "PIN (saved; leave blank to keep)" : "PIN (4–8 digits, for refresh)", text: $fyersPin)
                    .keyboardType(.numberPad)
                Button { Task { await saveFyers() } } label: { BusyLabel(title: "Save app details", systemImage: "square.and.arrow.down", busy: busy == "fyers-save") }
                    .disabled(busy != nil || fyersAppId.trimmingCharacters(in: .whitespaces).count < 3
                              || fyersRedirect.trimmingCharacters(in: .whitespaces).count < 8 || !pinValid)
            }
        } footer: {
            Text("Saved by the engine in its runtime credentials. The secret and PIN are never shown again.")
        }
    }

    private var pinValid: Bool {
        let p = fyersPin.trimmingCharacters(in: .whitespaces)
        return p.isEmpty || (4...8).contains(p.count)
    }

    // MARK: Upstox

    @ViewBuilder
    private var upstoxSections: some View {
        let u = logins.value?.upstox
        Section {
            if let u {
                LabeledContent("Status") { StatusText(status: u.status, bad: u.tokenExpired == true) }
                LabeledContent("Access token", value: tokenLine(saved: u.tokenPresent, expired: u.tokenExpired, expires: u.tokenExpiresAt))
                if let f = u.fields {
                    LabeledContent("Saved", value: ["api_key": "API key", "secret": "secret", "redirect_uri": "redirect", "analytics_token": "analytics token"]
                        .filter { f[$0.key] == true }.map(\.value).sorted().joined(separator: ", ").ifEmpty("nothing"))
                }
                if let d = u.detail { Text(d).font(.caption).foregroundStyle(.secondary) }
            } else if let err = logins.error {
                Label(err, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
            } else {
                ProgressView()
            }
            if let upstoxResult { OutcomeText(outcome: upstoxResult) }
        } header: {
            Text("Upstox · platform console")
        } footer: {
            Text("Through the market-intelligence console at \(model.consoleURL) (change it in Settings).")
        }

        Section {
            Button { Task { await openLogin(.upstox) } } label: { BusyLabel(title: "Log in to Upstox", systemImage: "person.badge.key", busy: busy == "upstox-url") }
                .disabled(busy != nil)
            SecureField("Redirect address or code", text: $upstoxPaste).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button { Task { await pasteUpstox() } } label: { BusyLabel(title: "Send to the console", systemImage: "paperplane", busy: busy == "upstox-paste") }
                .disabled(busy != nil || upstoxPaste.trimmingCharacters(in: .whitespacesAndNewlines).count < 4)
        } footer: {
            Text("After the Upstox login, the app catches the redirect to \(u?.redirectUri ?? "the app's redirect URL") and hands its code to the console.")
        }

        Section {
            DisclosureGroup("App details") {
                SecureField(u?.fields?["api_key"] == true ? "API key (saved; blank keeps it)" : "API key", text: $upstoxKey)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField(u?.fields?["secret"] == true ? "Secret (saved; blank keeps it)" : "Secret", text: $upstoxSecret)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Redirect URL", text: $upstoxRedirect).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField(u?.fields?["analytics_token"] == true ? "Analytics token (saved; blank keeps it)" : "Analytics token", text: $upstoxAnalytics)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Button { Task { await saveUpstox() } } label: { BusyLabel(title: "Save app details", systemImage: "square.and.arrow.down", busy: busy == "upstox-save") }
                    .disabled(busy != nil || [upstoxKey, upstoxSecret, upstoxAnalytics].allSatisfy { $0.trimmingCharacters(in: .whitespaces).isEmpty }
                              && upstoxRedirect.trimmingCharacters(in: .whitespaces) == (u?.redirectUri ?? ""))
            }
        }
    }

    // MARK: Actions

    private func load() async {
        await fyers.load { try await model.api().get("api/settings") }
        if let s = fyers.value {
            if fyersAppId.isEmpty { fyersAppId = s.fyersClientId ?? "" }
            if fyersRedirect.isEmpty { fyersRedirect = s.fyersRedirectUri ?? "" }
        }
        await logins.load { try await model.console().get("api/logins") }
        if upstoxRedirect.isEmpty, let r = logins.value?.upstox?.redirectUri { upstoxRedirect = r }
    }

    private func run(_ key: String, _ broker: BrokerLogin.Broker, _ action: () async throws -> String) async {
        busy = key
        defer { busy = nil }
        let outcome: Outcome
        do { outcome = Outcome(ok: true, text: try await action()) } catch { outcome = Outcome(ok: false, text: error.localizedDescription) }
        if broker == .fyers { fyersResult = outcome } else { upstoxResult = outcome }
        await load()
    }

    private func installed(_ r: FyersInstall) -> String {
        "Saved. Feed \(r.connectState ?? "reconnecting"); token valid to \(Fmt.dateTime(r.acceptedTokenExpiresAt))."
    }

    private func refreshFyers() async {
        await run("fyers-refresh", .fyers) { installed(try await model.api().post("api/auth/fyers/refresh")) }
    }

    private func pasteFyers() async {
        let pasted = fyersPaste.trimmingCharacters(in: .whitespacesAndNewlines)
        fyersPaste = ""
        await run("fyers-paste", .fyers) { installed(try await model.api().post("api/auth/fyers/access-token", body: ["access_token": pasted])) }
    }

    private func saveFyers() async {
        var body: [String: Any] = ["client_id": fyersAppId.trimmingCharacters(in: .whitespaces),
                                   "redirect_uri": fyersRedirect.trimmingCharacters(in: .whitespaces)]
        let secret = fyersSecret.trimmingCharacters(in: .whitespacesAndNewlines), pin = fyersPin.trimmingCharacters(in: .whitespaces)
        if !secret.isEmpty { body["secret"] = secret }
        if !pin.isEmpty { body["pin"] = pin }
        fyersSecret = ""; fyersPin = ""
        await run("fyers-save", .fyers) {
            let _: Ignored = try await model.api().post("api/auth/fyers/credentials", body: body)
            return "App details saved\(pin.isEmpty ? "" : ", PIN included")."
        }
    }

    private func pasteUpstox() async {
        let pasted = upstoxPaste.trimmingCharacters(in: .whitespacesAndNewlines)
        upstoxPaste = ""
        await run("upstox-paste", .upstox) {
            let _: Ignored = try await model.console().post("api/logins/upstox", body: ["pasted": pasted])
            return "Saved by the console."
        }
    }

    private func saveUpstox() async {
        var body: [String: Any] = [:]
        for (k, v) in [("api_key", upstoxKey), ("secret", upstoxSecret), ("redirect_uri", upstoxRedirect), ("analytics_token", upstoxAnalytics)] {
            let t = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { body[k] = t }
        }
        upstoxKey = ""; upstoxSecret = ""; upstoxAnalytics = ""
        await run("upstox-save", .upstox) {
            let _: Ignored = try await model.console().post("api/brokers/upstox/credentials", body: body)
            return "App details saved by the console."
        }
    }

    private func openLogin(_ broker: BrokerLogin.Broker) async {
        await run(broker == .fyers ? "fyers-url" : "upstox-url", broker) {
            if broker == .fyers {
                let r: AuthURL = try await model.api().get("api/auth/fyers/url")
                guard let url = URL(string: r.authUrl), BrokerLogin.trusted(url, domain: "fyers.in") else {
                    throw APIError("The engine's login address isn't a fyers.in page, so it wasn't opened.")
                }
                login = BrokerLogin(broker: .fyers, url: url, redirect: (r.redirectUri ?? fyers.value?.fyersRedirectUri).flatMap(URL.init(string:)),
                                    param: "auth_code")
            } else {
                let r: AuthURL = try await model.console().get("api/logins/upstox/url")
                guard let url = URL(string: r.authUrl), BrokerLogin.trusted(url, domain: "upstox.com") else {
                    throw APIError("The console's login address isn't an upstox.com page, so it wasn't opened.")
                }
                login = BrokerLogin(broker: .upstox, url: url, redirect: logins.value?.upstox?.redirectUri.flatMap(URL.init(string:)), param: "code")
            }
            return "Login page opened."
        }
    }

    private func finish(_ broker: BrokerLogin.Broker, redirect: Result<URL, APIError>) async {
        switch redirect {
        case .failure(let e):
            if broker == .fyers { fyersResult = Outcome(ok: false, text: e.message) } else { upstoxResult = Outcome(ok: false, text: e.message) }
        case .success(let url):
            if broker == .fyers {
                await run("fyers-paste", .fyers) { installed(try await model.api().post("api/auth/fyers/exchange", body: ["auth_code": url.absoluteString])) }
            } else {
                await run("upstox-paste", .upstox) {
                    let _: Ignored = try await model.console().post("api/logins/upstox", body: ["pasted": url.absoluteString])
                    return "Logged in; the console saved the Upstox token."
                }
            }
        }
    }

    private func tokenLine(saved: Bool?, expired: Bool?, expires: String?) -> String {
        guard saved == true else { return "none" }
        if expired == true { return "expired \(Fmt.dateTime(expires))" }
        return expires.map { "valid to \(Fmt.dateTime($0))" } ?? "saved"
    }
}

private extension String {
    func ifEmpty(_ fallback: String) -> String { isEmpty ? fallback : self }
}

private struct StatusText: View {
    let status: String?
    let bad: Bool
    var body: some View {
        let s = status ?? "unknown"
        let color: Color = bad ? .orange : ["connected", "configured"].contains(s) ? .gain : s == "connecting" ? .yellow : .secondary
        HStack(spacing: 6) { Circle().fill(color).frame(width: 8, height: 8); Text(s.capitalized) }
    }
}

private struct OutcomeText: View {
    let outcome: BrokersView.Outcome
    var body: some View {
        Label(outcome.text, systemImage: outcome.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .font(.callout).foregroundStyle(outcome.ok ? Color.gain : .orange)
    }
}

private struct BusyLabel: View {
    let title: String
    let systemImage: String
    let busy: Bool
    var body: some View {
        HStack { Label(title, systemImage: systemImage); if busy { Spacer(); ProgressView() } }
    }
}

// MARK: - Login web view

private struct BrokerLoginSheet: View {
    let login: BrokerLogin
    let onDone: (Result<URL, APIError>) -> Void
    @State private var loading = true
    @State private var host = ""
    @State private var secure = false

    var body: some View {
        NavigationStack {
            LoginWebView(login: login, loading: $loading, host: $host, secure: $secure, onDone: onDone)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(login.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { onDone(.failure(APIError("Login cancelled."))) } }
                    ToolbarItem(placement: .principal) {
                        HStack(spacing: 4) {
                            if secure { Image(systemName: "lock.fill").font(.caption2) }
                            Text(host).font(.caption.monospaced()).lineLimit(1)
                            if loading { ProgressView().controlSize(.mini) }
                        }
                        .foregroundStyle(.secondary)
                    }
                }
        }
        .interactiveDismissDisabled()
    }
}

/// A private (non-persistent) web view that ends the login when the broker redirects to the app's redirect URL.
private struct LoginWebView: UIViewRepresentable {
    let login: BrokerLogin
    @Binding var loading: Bool
    @Binding var host: String
    @Binding var secure: Bool
    let onDone: (Result<URL, APIError>) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.load(URLRequest(url: login.url))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) { context.coordinator.parent = self }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: LoginWebView
        private var finished = false
        init(_ parent: LoginWebView) { self.parent = parent }

        /// True when `url` is the redirect target (same host, port and path), or carries the code when no redirect is known.
        private func isRedirect(_ url: URL) -> Bool {
            if let r = parent.login.redirect, let rh = r.host {
                return url.host?.lowercased() == rh.lowercased() && url.port == r.port
                    && url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == r.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            }
            return code(in: url) != nil
        }

        private func code(in url: URL) -> String? {
            URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == parent.login.param }?.value
        }

        private func finish(_ url: URL) {
            guard !finished else { return }
            finished = true
            if let c = code(in: url), !c.isEmpty {
                parent.onDone(.success(url))
            } else {
                parent.onDone(.failure(APIError("The broker came back without a code (\(parent.login.param)); the login did not complete.")))
            }
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
            guard let url = navigationAction.request.url else { return .allow }
            if isRedirect(url) { finish(url); return .cancel }
            return .allow
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            parent.loading = true
            parent.host = webView.url?.host ?? ""
            parent.secure = webView.url?.scheme == "https"
        }

        func webView(_ webView: WKWebView, didReceiveServerRedirectForProvisionalNavigation navigation: WKNavigation!) {
            if let url = webView.url, isRedirect(url) { webView.stopLoading(); finish(url) }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.loading = false
            parent.host = webView.url?.host ?? ""
            parent.secure = webView.url?.scheme == "https" && webView.hasOnlySecureContent
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            parent.loading = false
            // A redirect to a local callback nobody serves fails to load; the address still carries the code.
            if let failing = (error as NSError).userInfo[NSURLErrorFailingURLErrorKey] as? URL, isRedirect(failing) { finish(failing) }
        }
    }
}
